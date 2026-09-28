"""업데이트 확인·교체·복구 계약 시험 (설계 U1 r3 §8·§11, REVIEW-U1-D-r3 §4).

실제 GitHub 호출은 0이다. check/apply는 connect·download 팩토리 주입,
프로세스 절단 시험은 subprocess 드라이버가 모듈 훅을 감은 뒤 os._exit한다.
모든 임시 파일은 tempfile 아래에 둔다(경로는 resolve — /tmp 링크 대응).
"""
import hashlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, REPO_ROOT, runtime

SKILL_DIR = (REPO_ROOT / "skills" / "knuaf-doc").resolve()
SCRIPTS_DIR = SKILL_DIR / "scripts"
SKILL_MD_PATH = SKILL_DIR / "SKILL.md"
UPDATE_MD_PATH = SKILL_DIR / "references" / "update.md"
UPDATE_SCRIPT = SCRIPTS_DIR / "gg_update.py"
VERSION_JSON_PATH = SKILL_DIR / "version.json"
PLUGIN_JSON_PATH = REPO_ROOT / ".codex-plugin" / "plugin.json"

NL = chr(10)
BS = chr(92)
NUL = chr(0)

SKILL_MD_MIN = """---
name: knuaf-doc
description: t
---
# t
"""

REPORT_STUB = "# report marker" + NL


def _version_doc(version, *, repo="starceas/knuaf-doc",
                 schema="knuaf-doc/version@1"):
    return {"schema": schema, "version": version, "repo": repo}


def _write_skill(root, *, version=None, repo="starceas/knuaf-doc",
                 name="knuaf-doc", marker=True, updater=False,
                 real_report=False):
    """최소 knuaf-doc 스킬 트리. version=None이면 0.1.0 legacy 설치본."""
    root = Path(root)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    text = SKILL_MD_MIN if name == "knuaf-doc" else SKILL_MD_MIN.replace(
        "name: knuaf-doc", "name: " + name)
    (root / "SKILL.md").write_text(text, encoding="utf-8")
    if version is not None:
        (root / "version.json").write_text(
            json.dumps(_version_doc(version, repo=repo)),
            encoding="utf-8")
    if real_report:
        shutil.copy2(SCRIPTS_DIR / "gg_report.py",
                     root / "scripts" / "gg_report.py")
    elif marker:
        (root / "scripts" / "gg_report.py").write_text(
            REPORT_STUB, encoding="utf-8")
    if updater:
        shutil.copy2(UPDATE_SCRIPT, root / "scripts" / "gg_update.py")
    return root


def _tree_bytes(root):
    root = Path(root)
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and not p.is_symlink()}


_seq = [0]


def _load_update(script):
    """스테이징된 gg_update.py 사본을 고유 모듈명으로 로드한다."""
    _seq[0] += 1
    spec = importlib.util.spec_from_file_location(
        "gg_update_staged_%d" % _seq[0], str(script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_module(path, tag):
    _seq[0] += 1
    spec = importlib.util.spec_from_file_location(
        "staged_%s_%d" % (tag, _seq[0]), str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self._headers = list(headers or [])
        self._body = io.BytesIO(body)

    def getheaders(self):
        return list(self._headers)

    def read(self, n=-1):
        return self._body.read(n)


class _FakeConn:
    def __init__(self, resp=None, error=None, delay=0.0):
        self.resp = resp
        self.error = error
        self.delay = delay

    def request(self, *args, **kwargs):
        if self.error is not None and self.resp is None:
            raise self.error

    def getresponse(self):
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.resp


def _connect(status=None, headers=None, body=b"", error=None, delay=0.0):
    """한 호스트만 응답하는 connect 팩토리."""
    resp = _FakeResp(status, headers, body) if status is not None else None

    def factory(host, timeout):
        return _FakeConn(resp=resp, error=error, delay=delay)

    return factory


def _connect_mux(resp_by_host):
    """host별 응답을 연결하는 connect 팩토리(github + codeload)."""

    def factory(host, timeout):
        return _FakeConn(resp=resp_by_host.get(host))

    return factory


def _loc(version):
    return "/starceas/knuaf-doc/releases/tag/v" + version


def _zi(name, data=b"", *, mode=0o100644, compress=zipfile.ZIP_DEFLATED):
    info = zipfile.ZipInfo(name)
    info.external_attr = mode << 16
    info.compress_type = compress
    return info


def _zip(items):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for info, data in items:
            zf.writestr(info, data)
    return buf.getvalue()


def _zip_with_extra(name, data=b"", *, mode=0o100644,
                    compress=zipfile.ZIP_DEFLATED):
    """정상 새 트리 zip(_good_zip)에 위반 항목 하나를 더한 fixture.

    정상 트리를 유지한 채 조건 하나만 위반하므로 거절이 그 조건에서
    나왔음이 보장된다."""
    buf = io.BytesIO(_good_zip())
    with zipfile.ZipFile(buf, "a", compression=compress) as zf:
        zf.writestr(_zi(name, mode=mode, compress=compress), data)
    return buf.getvalue()


def _good_zip(version="0.1.2", top=None):
    """git archive --prefix 형태(디렉터리 항목 포함)의 정상 저장소 zip."""
    top = top or ("knuaf-doc-" + version)
    vjson = json.dumps(_version_doc(version)).encode("utf-8")
    return _zip([
        (_zi(top + "/", mode=0o40755), b""),
        (_zi(top + "/skills/", mode=0o40755), b""),
        (_zi(top + "/skills/knuaf-doc/", mode=0o40755), b""),
        (_zi(top + "/skills/knuaf-doc/SKILL.md"),
         SKILL_MD_MIN.encode("utf-8")),
        (_zi(top + "/skills/knuaf-doc/version.json"), vjson),
        (_zi(top + "/skills/knuaf-doc/scripts/", mode=0o40755), b""),
        (_zi(top + "/skills/knuaf-doc/scripts/gg_report.py"),
         REPORT_STUB.encode("utf-8")),
        (_zi(top + "/skills/knuaf-doc/scripts/gg_update.py"),
         UPDATE_SCRIPT.read_bytes()),
        (_zi(top + "/README.md"), b"# r" + NL.encode("utf-8")),
    ])


def _corrupt_entry_bytes(data, victim_name):
    """STORED 항목의 데이터 1바이트를 뒤집어 CRC 오류를 만든다."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = zf.getinfo(victim_name)
        assert info.compress_type == zipfile.ZIP_STORED
    off = info.header_offset
    nlen, elen = struct.unpack_from("<HH", data, off + 26)
    start = off + 30 + nlen + elen
    arr = bytearray(data)
    arr[start] ^= 0xFF
    return bytes(arr)


# subprocess 절단 드라이버: 모듈 훅을 감은 뒤 지정 절단점에서 os._exit한다.
# 인자: driver.py <gg_update.py> adopt <cut> <source> <target>
#       driver.py <gg_update.py> recover <cut> <home>
# cut: wj<N>(N번째 journal 기록 직후), rn<N>(N번째 rename 직후),
#      pw<S>(prepared journal 기록 직후 S초 수면 — lock은 계속 소유),
#      rbk(rename(2) 실패 → rollback rename 성공 직후 절단),
#      hold<S>(lock 획득 뒤 S초 수면), '-'(절단 없음)
_DRIVER = """
import importlib.util, json, os, sys, time

script, mode, cut = sys.argv[1], sys.argv[2], sys.argv[3]
rest = sys.argv[4:]
spec = importlib.util.spec_from_file_location("gu_driver", script)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

if cut.startswith("wj"):
    n = int(cut[2:]); calls = [0]; orig = mod._write_journal
    def wj(sd, doc):
        calls[0] += 1
        orig(sd, doc)
        if calls[0] == n:
            os._exit(0)
    mod._write_journal = wj
elif cut.startswith("rn"):
    n = int(cut[2:]); calls = [0]; orig = mod._rename
    def rn(a, b):
        orig(a, b)
        calls[0] += 1
        if calls[0] == n:
            os._exit(0)
    mod._rename = rn
elif cut.startswith("pw"):
    secs = float(cut[2:]); orig = mod._write_journal
    def pw(sd, doc):
        orig(sd, doc)
        if doc.get("phase") == "prepared":
            time.sleep(secs)
    mod._write_journal = pw
elif cut == "rbk":
    calls = [0]; orig = mod._rename
    def rbk(a, b):
        calls[0] += 1
        if calls[0] == 2:
            raise OSError("rename(2) injected failure")
        orig(a, b)
        if calls[0] == 3:
            os._exit(0)  # rollback 성공 직후, phase 기록 전 절단
    mod._rename = rbk
elif cut.startswith("hold"):
    secs = float(cut[4:]); orig = mod._acquire_lock
    def al(sd):
        r = orig(sd)
        time.sleep(secs)
        return r
    mod._acquire_lock = al

if mode == "adopt":
    out = mod.adopt_cmd(rest[0], rest[1], confirm=True)
elif mode == "apply":
    out = mod.apply_cmd(rest[0], confirm=True)
elif mode == "recover":
    out = mod.recover_cmd(rest[0], confirm=True)
else:
    out = {"status": "error", "reason": "bad_mode"}
print(json.dumps(out, ensure_ascii=False))
"""



# File-signal barriers fix the intervention order; polling delay is not a race oracle.
_BARRIER_DRIVER = r"""
import importlib.util, json, os, subprocess, sys, time
from pathlib import Path
script, mode, home, source, target, ready, go = sys.argv[1:]
ready, go = Path(ready), Path(go)
spec = importlib.util.spec_from_file_location('barrier_update', script)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def pause(doc):
    ready.write_text(json.dumps(doc), encoding='utf-8')
    while not go.exists():
        time.sleep(0.005)
if mode == 'lock':
    fd = m._acquire_lock(Path(home) / m.STATE_DIR_NAME)
    try: pause({'pid': os.getpid()})
    finally: m._release_lock(fd)
elif mode == 'parent_with_child':
    fd = m._acquire_lock(Path(home) / m.STATE_DIR_NAME)
    child_ready = ready.with_name(ready.name + '-child')
    child = subprocess.Popen([sys.executable, '-c',
        'from pathlib import Path; import sys,time; '
        'Path(sys.argv[1]).write_text("{}"); '
        'p=Path(sys.argv[2]); '
        'exec("while not p.exists(): time.sleep(0.005)")',
        str(child_ready), str(go)], close_fds=True)
    pause({'pid': os.getpid(), 'child': child.pid})
elif mode == 'recover':
    original = m._recover_locked
    def held(j, h, st):
        pause({'txid': j['txid'], 'pid': os.getpid()})
        return original(j, h, st)
    m._recover_locked = held
    print(json.dumps(m.recover_cmd(home, confirm=True)), flush=True)
elif mode == 'adopt':
    original = m._write_journal
    def held(st, j):
        original(st, j)
        if j['phase'] == 'prepared':
            pause({'txid': j['txid'], 'pid': os.getpid()})
    m._write_journal = held
    print(json.dumps(m.adopt_cmd(source, target, confirm=True)), flush=True)
"""


def _wait_signal(path, proc):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if proc.poll() is not None:
            raise AssertionError("barrier subprocess exited: %s" % proc.communicate()[1])
        time.sleep(0.005)
    raise AssertionError("barrier timeout")

class _UpdateCase(ContractCase):
    def setUp(self):
        self.gu = runtime("gg_update")
        self._tmp = tempfile.TemporaryDirectory(prefix="u1-update-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def tmpdir(self, name):
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def make_target(self, version="0.1.1", name="knuaf-doc", **kw):
        """<home>/skills/<name> 설치본 → (home, skills_dir, target)."""
        home = Path(tempfile.mkdtemp(prefix="home-", dir=str(self.root)))
        skills = home / "skills"
        target = _write_skill(skills / name, version=version,
                              name=name, **kw)
        return home, skills, target

    def make_incoming(self, version="0.1.2", name="knuaf-doc", **kw):
        """skills 밖 incoming 사본 → 소스 스킬 루트."""
        base = Path(tempfile.mkdtemp(prefix="incoming-", dir=str(self.root)))
        src = _write_skill(base / "knuaf-doc", version=version, name=name,
                           updater=True, **kw)
        return src


class UpdateTests(_UpdateCase):
    def _apply(self, target, *, version="0.1.2", payload=None, loc=None):
        mod = _load_update(target / "scripts" / "gg_update.py")
        conn = _connect(302, [("Location", loc or _loc(version))])
        result = mod.apply_cmd(version, confirm=True, connect=conn,
                               download=lambda _v, _c: payload or _good_zip(version))
        return mod, result

    def test_version_contract_and_integer_order(self):
        self.assertEqual((0, 10, 0), self.gu.parse_version("0.10.0"))
        self.assertGreater(self.gu.parse_version("0.10.0"),
                           self.gu.parse_version("0.9.0"))
        for bad in ("01.0.0", "0.00.0", "0.0.0000000000", "٠.1.0",
                    "0.1.0 ", "v0.1.0", "0.1", "0.1.-1", None):
            self.assertIsNone(self.gu.parse_version(bad))
        version = json.loads(VERSION_JSON_PATH.read_text(encoding="utf-8"))
        plugin = json.loads(PLUGIN_JSON_PATH.read_text(encoding="utf-8"))
        self.assertEqual({"schema", "version", "repo"}, set(version))
        self.assertEqual(plugin["version"], version["version"])
        self.assertEqual("0.1.1", version["version"])
        for raw in ('{"schema":"knuaf-doc/version@1","version":"0.1.1",'
                    '"repo":"starceas/knuaf-doc","repo":"starceas/knuaf-doc"}',
                    '{"schema":"bad","version":"0.1.1","repo":"starceas/knuaf-doc"}',
                    '{"schema":"knuaf-doc/version@1","version":1,"repo":"starceas/knuaf-doc"}',
                    '{"schema":"knuaf-doc/version@1","version":"0.1.1","repo":"other"}'):
            p = self.root / "bad-version.json"
            p.write_text(raw, encoding="utf-8")
            with self.assertRaises(self.gu.VersionUnreadable):
                self.gu.read_version_file(p)

    def test_check_status_matrix_and_location_validation(self):
        _, _, target = self.make_target()
        valid = _loc("0.1.2")
        for status, location, expected in (
            (302, valid, "update_available"),
            (302, _loc("0.1.1"), "up_to_date"),
            (302, _loc("0.1.0"), "local_newer"),
            (302, "/starceas/knuaf-doc/releases", "no_release"),
            (302, "https://GITHUB.com" + valid, "update_available"),
            (200, valid, "unknown"), (404, valid, "unknown"),
            (500, valid, "unknown"), (301, valid, "unknown"),
            (302, None, "unknown"),
        ):
            headers = [] if location is None else [("Location", location)]
            with self.subTest(status=status, location=location):
                out = self.gu.check_status(connect=_connect(status, headers),
                                            local_root=target)
                self.assertEqual(expected, out["status"])
                self.assertEqual("copy", out["install_kind"])
        bad = ("https://github.com:443" + valid,
               "https://user@github.com" + valid,
               "https://github.com." + valid,
               "http://github.com" + valid, "//github.com" + valid,
               "releases/tag/v0.1.2", valid + "?x=1", valid + "#a",
               valid.replace("/tag/", "/TAG/"), valid + "%32",
               valid + " ", valid + "\n", valid + "é", "x" * 257,
               "/starceas/knuaf-doc/releases/tag/v01.1.2")
        for loc in bad:
            with self.subTest(loc=loc):
                self.assertEqual("unknown", self.gu.check_status(
                    connect=_connect(302, [("Location", loc)]),
                    local_root=target)["status"])
        self.assertEqual("unknown", self.gu.check_status(
            connect=_connect(302, [("Location", valid), ("location", valid)]),
            local_root=target)["status"])
        self.assertEqual("offline", self.gu.check_status(
            connect=_connect(error=OSError("secret-path")),
            local_root=target)["status"])
        out = self.gu.check_status(connect=_connect(error=OSError("secret-path")),
                                   local_root=target)
        self.assertNotIn("secret-path", json.dumps(out))
        out = self.gu.check_status(connect=_connect(302, [("Location", valid)],
                                                    delay=0.05),
                                   deadline=0.005, local_root=target)
        self.assertEqual("offline", out["status"])
        (target / "version.json").write_text("broken", encoding="utf-8")
        self.assertEqual("no_release", self.gu.check_status(
            connect=_connect(302, [("Location", "/starceas/knuaf-doc/releases")]),
            local_root=target)["status"])
        self.assertEqual("local_unreadable", self.gu.check_status(
            connect=_connect(302, [("Location", valid)]),
            local_root=target)["reason"])
        self.assertEqual("offline", self.gu.check_status(
            connect=_connect(error=OSError("offline")),
            local_root=target)["status"])

    def test_cli_check_single_json_and_usage_error(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(0, self.gu.main(["check"], connect=_connect(
                302, [("Location", "/starceas/knuaf-doc/releases")])))
        self.assertEqual("no_release", json.loads(buf.getvalue())["status"])
        self.assertEqual(1, len(buf.getvalue().splitlines()))
        with self.assertRaises(SystemExit) as caught:
            self.gu.main(["apply"])
        self.assertEqual(2, caught.exception.code)

    def test_install_classification_and_target_guards(self):
        _, _, target = self.make_target()
        self.assertEqual("copy", self.gu.classify_install(target))
        (target / ".git").mkdir()
        self.assertEqual("git", self.gu.classify_install(target))
        (target / ".git").rmdir()
        cache = self.root / "plugins" / "cache" / "skills" / "knuaf-doc"
        cache.mkdir(parents=True)
        (cache / ".git").mkdir()
        self.assertEqual("plugin_cache", self.gu.classify_install(cache))
        self.assertEqual("unknown", self.gu.classify_install(self.root))
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        before = _tree_bytes(target)
        self.assertEqual("source_target_alias", mod.adopt_cmd(
            src, src, confirm=True)["reason"])
        self.assertEqual(before, _tree_bytes(target))
        other_home, skills, other = self.make_target(name="other-skill")
        self.assertEqual("target_not_knuaf_doc", mod.adopt_cmd(
            src, other, confirm=True)["reason"])
        link = other_home / "linked-skills"
        link.symlink_to(target.parent, target_is_directory=True)
        self.assertEqual("symlinked_path", mod.adopt_cmd(
            src, link / "knuaf-doc", confirm=True)["reason"])

    def test_adopt_legacy_and_version_rejections(self):
        src = self.make_incoming(real_report=True)
        mod = _load_update(src / "scripts" / "gg_update.py")
        _, _, target = self.make_target(version=None)
        out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("applied", out["status"], out)
        self.assertEqual((0, 1, 2), mod.read_version_file(target / "version.json"))
        self.assertTrue(Path(out["backup"]).is_dir())
        self.assertEqual("not_newer", mod.adopt_cmd(
            src, target, confirm=True)["reason"])
        for kw in ({"version": "0.1.3"}, {"version": "0.1.1", "repo": "other"}):
            _, _, t = self.make_target(**kw)
            before = _tree_bytes(t)
            self.assertEqual("target_version_invalid" if kw.get("repo") else "not_newer",
                             mod.adopt_cmd(src, t, confirm=True)["reason"])
            self.assertEqual(before, _tree_bytes(t))
        _, _, t = self.make_target()
        (t / "version.json").write_text("{", encoding="utf-8")
        self.assertEqual("target_version_invalid", mod.adopt_cmd(
            src, t, confirm=True)["reason"])

    def test_apply_git_archive_shape_and_not_latest(self):
        _, _, target = self.make_target(updater=True)
        mod, out = self._apply(target)
        self.assertEqual("applied", out["status"], out)
        self.assertEqual((0, 1, 2), mod.read_version_file(target / "version.json"))
        self.assertEqual("not_newer", self._apply(target)[1]["reason"])
        _, _, t = self.make_target(updater=True)
        self.assertEqual("not_latest", self._apply(
            t, loc=_loc("0.1.3"))[1]["reason"])

    def test_zip_rejections_leave_installed_tree(self):
        """정상 새 트리를 유지한 채 조건 하나만 위반한 fixture가 정확한
        reason/detail로 거절되고 기존 설치를 보존하는지 본다."""
        top = "knuaf-doc-0.1.2"
        single = (
            (top + "/skills/knuaf-doc/../evil", b"x", {}, "bad_component"),
            ("/" + top + "/skills/knuaf-doc/x", b"x", {}, "absolute"),
            (top + "/skills/knuaf-doc" + BS + "x", b"x", {}, "bad_chars"),
            ("C:" + top + "/skills/knuaf-doc/x", b"x", {}, "bad_chars"),
            ("other-top/x", b"x", {}, "top_folder"),
            (top + "/skills/knuaf-doc/skill.md", b"x", {}, "duplicate"),
            (top + "/skills/knuaf-doc/scripts/gg_report.py/x",
             b"x", {}, "file_dir_conflict"),
            (top + "/skills/knuaf-doc/d//e", b"x", {}, "bad_component"),
            (top + "/skills/knuaf-doc/trail//", b"x",
             {"mode": 0o40755}, "bad_component"),
            (top + "/skills/knuaf-doc/dot/.", b"x", {}, "bad_component"),
            ("", b"x", {}, "bad_component"),
            (top + "/skills/knuaf-doc/x" * 26, b"x", {}, "name_too_long"),
            (top + "/x/", b"x", {"mode": 0o100644}, "dir_declared_file"),
            (top + "/x", b"x", {"mode": 0o120777}, "entry_type"),
        )
        for name, data, kw, detail in single:
            with self.subTest(name=name, detail=detail):
                _, _, target = self.make_target(updater=True)
                before = _tree_bytes(target)
                payload = _zip_with_extra(name, data, **kw)
                out = self._apply(target, payload=payload)[1]
                self.assertEqual("refused", out["status"], out)
                self.assertEqual("zip_invalid", out["reason"], out)
                self.assertEqual(detail, out.get("detail"), out)
                self.assertEqual(before, _tree_bytes(target))
        # 파일/디렉터리 충돌 반대 방향: 자손이 먼저 오고 조상이 파일로 충돌.
        _, _, target = self.make_target(updater=True)
        before = _tree_bytes(target)
        buf = io.BytesIO(_good_zip())
        with zipfile.ZipFile(buf, "a") as zf:
            zf.writestr(_zi(top + "/d2/f"), b"x")
            zf.writestr(_zi(top + "/d2"), b"x")
        out = self._apply(target, payload=buf.getvalue())[1]
        self.assertEqual("file_dir_conflict", out.get("detail"), out)
        self.assertEqual(before, _tree_bytes(target))

        # 파일 크기 상한 — 보호를 제거하면 같은 payload가 적용된다(negative control).
        bomb = _zip_with_extra(top + "/skills/knuaf-doc/huge",
                               b"x" * (self.gu.ZIP_FILE_MAX + 1))
        _, _, target = self.make_target(updater=True)
        before = _tree_bytes(target)
        out = self._apply(target, payload=bomb)[1]
        self.assertEqual(("zip_invalid", "size_limit"),
                         (out["status"] == "refused" and out["reason"],
                          out.get("detail")), out)
        self.assertEqual(before, _tree_bytes(target))
        _, _, control = self.make_target(updater=True)
        mod = _load_update(control / "scripts" / "gg_update.py")
        with mock.patch.object(mod, "ZIP_FILE_MAX", 10 ** 12):
            conn = _connect(302, [("Location", _loc("0.1.2"))])
            out = mod.apply_cmd("0.1.2", confirm=True, connect=conn,
                                download=lambda *_: bomb)
            self.assertEqual("applied", out["status"], out)

        # 합계 상한 — 작은 상한은 거절하고, 상한을 올린 대조군은 적용된다.
        _, _, target = self.make_target(updater=True)
        before = _tree_bytes(target)
        mod = _load_update(target / "scripts" / "gg_update.py")
        with mock.patch.object(mod, "ZIP_TOTAL_MAX", 128):
            conn = _connect(302, [("Location", _loc("0.1.2"))])
            out = mod.apply_cmd("0.1.2", confirm=True, connect=conn,
                                download=lambda *_: _good_zip())
            self.assertEqual(("refused", "zip_invalid", "size_limit"),
                             (out["status"], out.get("reason"),
                              out.get("detail")), out)
            self.assertEqual(before, _tree_bytes(target))
        with mock.patch.object(mod, "ZIP_TOTAL_MAX", 10 ** 12):
            out = mod.apply_cmd("0.1.2", confirm=True,
                connect=_connect(302, [("Location", _loc("0.1.2"))]),
                download=lambda *_: _good_zip())
            self.assertEqual("applied", out["status"], out)

        # 항목 수 상한 — 상한을 올리면 같은 목록이 통과한다.
        with mock.patch.object(self.gu, "ZIP_MAX_ENTRIES", 1):
            with self.assertRaises(self.gu.Refused):
                self.gu._normalize_zip(zipfile.ZipFile(
                    io.BytesIO(_good_zip())).infolist())

        # CRC — 같은 파일을 유효하게 두면 적용된다(검사 제거 대조).
        _, _, target = self.make_target(updater=True)
        before = _tree_bytes(target)
        stored = _zip_with_extra(
            top + "/skills/knuaf-doc/x", b"abc",
            compress=zipfile.ZIP_STORED)
        corrupt = _corrupt_entry_bytes(
            stored, top + "/skills/knuaf-doc/x")
        out = self._apply(target, payload=corrupt)[1]
        self.assertEqual(("refused", "zip_invalid", "crc_error"),
                         (out["status"], out.get("reason"),
                          out.get("detail")), out)
        self.assertEqual(before, _tree_bytes(target))
        _, _, target = self.make_target(updater=True)
        self.assertEqual("applied",
                         self._apply(target, payload=stored)[1]["status"])

        # 다운로드 상한 — 본문 상한 초과는 거절, 대조군은 적용.
        _, _, target = self.make_target(updater=True)
        before = _tree_bytes(target)
        mod = _load_update(target / "scripts" / "gg_update.py")
        conn = _connect_mux({mod.CHECK_HOST: _FakeResp(302,
                             [("Location", _loc("0.1.2"))]),
                             mod.DOWNLOAD_HOST: _FakeResp(200, body=_good_zip())})
        with mock.patch.object(mod, "DOWNLOAD_MAX", 8):
            out = mod.apply_cmd("0.1.2", confirm=True, connect=conn,
                                download=lambda v, c: mod._default_download(
                                    v, c))
            self.assertEqual("refused", out["status"], out)
            self.assertEqual("download_failed", out.get("reason"), out)
        self.assertEqual(before, _tree_bytes(target))
        _, _, control = self.make_target(updater=True)
        mod = _load_update(control / "scripts" / "gg_update.py")
        with mock.patch.object(mod, "DOWNLOAD_MAX", 10 ** 12):
            conn = _connect_mux({mod.CHECK_HOST: _FakeResp(302,
                                 [("Location", _loc("0.1.2"))]),
                                 mod.DOWNLOAD_HOST: _FakeResp(200, body=_good_zip())})
            out = mod.apply_cmd("0.1.2", confirm=True, connect=conn,
                                download=lambda v, c: mod._default_download(v, c))
            self.assertEqual("applied", out["status"], out)

    def _assert_symlinked_recovery_bin_refused(self, legacy):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target(version=None if legacy else "0.1.1",
                                            updater=not legacy)
        outside = self.tmpdir("outside-%s" % legacy)
        state = home / "knuaf-doc-update"
        state.mkdir()
        (state / "bin").symlink_to(outside, target_is_directory=True)
        before, out_before = _tree_bytes(target), _tree_bytes(outside)
        if legacy:
            result = mod.adopt_cmd(src, target, confirm=True)
        else:
            result = self._apply(target)[1]
        self.assertEqual("unsafe_state_path", result["reason"])
        self.assertEqual(before, _tree_bytes(target))
        self.assertEqual(out_before, _tree_bytes(outside))

    def test_adopt_refuses_symlinked_recovery_bin(self):
        self._assert_symlinked_recovery_bin_refused(True)

    def test_apply_refuses_symlinked_recovery_bin(self):
        self._assert_symlinked_recovery_bin_refused(False)

    def test_recovery_helper_destination_never_overwrites(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target(version=None)
        state = home / "knuaf-doc-update"
        (state / "bin").mkdir(parents=True)
        fixed = "b" * 16
        helper = state / "bin" / ("gg_update-%s.py" % fixed)
        helper.write_text("untouched", encoding="utf-8")
        with mock.patch.object(mod, "_nonce", side_effect=[fixed]):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("helper_exists", out["reason"])
        self.assertEqual("untouched", helper.read_text(encoding="utf-8"))
        home2, _, target2 = self.make_target(version=None)
        state2 = home2 / "knuaf-doc-update"
        (state2 / "bin").mkdir(parents=True)
        external = self.root / "external-helper-target"
        external.write_text("outside", encoding="utf-8")
        (state2 / "bin" / ("gg_update-%s.py" % fixed)).symlink_to(external)
        with mock.patch.object(mod, "_nonce", side_effect=[fixed]):
            out = mod.adopt_cmd(src, target2, confirm=True)
        self.assertEqual("helper_exists", out["reason"])
        self.assertEqual("outside", external.read_text(encoding="utf-8"))

    def test_rename_failures_and_recovery_helper(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        for fail_on in (1, 2, 3):
            home, _, target = self.make_target(version=None)
            before = _tree_bytes(target)
            calls = [0]
            original = mod._rename
            def faulty(a, b):
                calls[0] += 1
                if calls[0] == fail_on or (fail_on == 3 and calls[0] == 2):
                    raise OSError("injected")
                return original(a, b)
            with mock.patch.object(mod, "_rename", side_effect=faulty):
                out = mod.adopt_cmd(src, target, confirm=True)
            self.assertEqual("failed", out["status"])
            if fail_on < 3:
                self.assertEqual(before, _tree_bytes(target))
            else:
                self.assertEqual("backup_preserved_target_missing", out["state"])
                self.assertFalse(target.exists())
                cmd = shlex.split(out["recover"])
                recovered = subprocess.run(cmd, cwd=str(self.root),
                                           capture_output=True, text=True, check=True)
                self.assertEqual("recovered_old", json.loads(
                    recovered.stdout)["status"])
                self.assertEqual(before, _tree_bytes(target))

    def test_legacy_recovery_helper_survives_interrupted_adopt(self):
        driver = self.root / "driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        src = self.make_incoming()
        for cut, expected in (("wj1", "not_started"),
                              ("rn1", "recovered_old"),
                              ("wj2", "recovered_old"),
                              ("rn2", "completed"),
                              ("wj3", "completed")):
            with self.subTest(cut=cut):
                home, _, target = self.make_target(version=None)
                old = _tree_bytes(target)
                proc = subprocess.run([sys.executable, str(driver),
                                       str(src / "scripts" / "gg_update.py"),
                                       "adopt", cut, str(src), str(target)],
                                      cwd=str(self.root), capture_output=True,
                                      text=True, check=True)
                self.assertEqual("", proc.stdout)
                helpers = list((home / "knuaf-doc-update" / "bin").glob("gg_update-*.py"))
                self.assertEqual(1, len(helpers))
                cmd = [sys.executable, str(helpers[0]), "recover", "--home",
                       str(home), "--confirm"]
                result = subprocess.run(cmd, cwd=str(self.root),
                                        capture_output=True, text=True, check=True)
                self.assertEqual(expected, json.loads(result.stdout)["status"])
                self.assertTrue((home / "knuaf-doc-update" / "lock").is_file())
                self.assertFalse((home / "knuaf-doc-update" / "journal.json").exists())
                self.assertEqual(1, len(list((home / "knuaf-doc-update").glob(
                    "journal-*-closed.json"))))
                if expected != "completed":
                    self.assertEqual(old, _tree_bytes(target))
                else:
                    self.assertEqual("0.1.2", json.loads((target / "version.json")
                        .read_text(encoding="utf-8"))["version"])

    def test_skill_guidance_required_phrases(self):
        raw = SKILL_MD_PATH.read_text(encoding="utf-8")
        self.assertIn(("> knuaf-doc · 창업논문 작성 도우미\n"
                       "> prod. 특용작물전공 24학번 김대욱\n"), raw)
        for sentence in (
            'next의 ready는 "진행할 수 있음"일 뿐 ✓의 근거가 아니다',
            'question이 reuse(제공 거부)·deferred(도움 소진)인 사실은 다시 묻거나 자료를 요구하지 않는다',
            'user_finish 전체',
            '독립 내용검토 전',
            '연속한 ✓ 단계가 2개 이상이면 `✓ 1–2단계 끝`처럼, 연속한 ○ 단계가 2개 이상이면 `○ 5–7단계 남음`처럼 항상 한 줄로 묶는다',
        ):
            self.assertIn(sentence, raw)
        for phrase in ("prod. 특용작물전공 24학번 김대욱", "최종 답변 맨 앞",
                       "스레드마다 한 번", "references/update.md", "자료·전공 확인",
                       "계획 인터뷰", "조사·근거 정리", "본문 Ⅰ~Ⅵ 초안",
                       "재무 엑셀", "검토·고치기", "워드 검토본",
                       "지금 확인할 것", "다음에 할 말", "상단 블록",
                       "10줄 이하", "독립 내용검토 전", "ready", "reuse",
                       "deferred", "user_finish", "그 뒤", "✓", "▶", "○", "⚑"):
            self.assertIn(phrase, raw)
        self.assertIn("논문 완성", raw)
        self.assertIn("제출 완료", raw)
        self.assertIn("업데이트해줘 / 업데이트 확인", raw)

    def test_path_and_lock_guards(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target(version=None)
        before = _tree_bytes(target)
        with mock.patch.object(mod.os, "getcwd", return_value=str(target)):
            self.assertEqual("cwd_inside_skill", mod.adopt_cmd(
                src, target, confirm=True)["reason"])
        self.assertEqual(before, _tree_bytes(target))
        state = home / "knuaf-doc-update"
        outside = self.tmpdir("state-outside")
        state.symlink_to(outside, target_is_directory=True)
        self.assertEqual("unsafe_state_path", mod.adopt_cmd(
            src, target, confirm=True)["reason"])
        state.unlink()
        state.mkdir()
        (state / "backups").symlink_to(outside, target_is_directory=True)
        self.assertEqual("unsafe_state_path", mod.adopt_cmd(
            src, target, confirm=True)["reason"])
        (state / "backups").unlink()
        self.assertEqual(before, _tree_bytes(target))

        lock = state / "lock"
        fd = mod._acquire_lock(state)
        self.assertTrue(lock.is_file())
        with self.assertRaises(mod.Refused) as caught:
            mod._acquire_lock(state)
        self.assertEqual("locked", caught.exception.reason)
        mod._release_lock(fd)
        self.assertTrue(lock.is_file())
        again = mod._acquire_lock(state)
        mod._release_lock(again)

    def test_changed_after_lock_is_refused(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target(version=None)
        original = mod._acquire_lock
        def race(state):
            result = original(state)
            (target / "version.json").write_text(
                json.dumps(_version_doc("0.1.0")), encoding="utf-8")
            return result
        with mock.patch.object(mod, "_acquire_lock", side_effect=race):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("changed_during_update", out["reason"])
        self.assertTrue(target.exists())
        self.assertEqual([], list((home / "knuaf-doc-update" / "backups").iterdir()))

    def test_cross_device_is_refused_before_rename(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target(version=None)
        state = home / "knuaf-doc-update"
        before = _tree_bytes(target)
        real_stat = os.stat
        def altered(path, *args, **kwargs):
            st = real_stat(path, *args, **kwargs)
            if str(path) == str(state):
                values = list(st)
                values[2] += 1  # st_dev in os.stat_result
                return os.stat_result(values)
            return st
        with mock.patch.object(mod.os, "stat", side_effect=altered):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("cross_device", out["reason"])
        self.assertEqual(before, _tree_bytes(target))

    def test_recover_refuses_forged_journal_and_manual_state(self):
        driver = self.root / "driver-manual.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        src = self.make_incoming()
        for forged in (False, True):
            home, _, target = self.make_target(version=None)
            subprocess.run([sys.executable, str(driver),
                            str(src / "scripts" / "gg_update.py"),
                            "adopt", "wj1", str(src), str(target)],
                           cwd=str(self.root), capture_output=True, check=True)
            state = home / "knuaf-doc-update"
            journal_path = state / "journal.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if forged:
                journal["backup"] = str(self.root / "outside-backup")
            else:
                backup = Path(journal["backup"])
                shutil.copytree(target, backup)
            journal_path.write_text(json.dumps(journal), encoding="utf-8")
            mod = _load_update(src / "scripts" / "gg_update.py")
            out = mod.recover_cmd(home, confirm=True)
            self.assertEqual("manual_required", out["status"])
            self.assertTrue(journal_path.exists())
            self.assertTrue((state / "lock").exists())

    def test_staging_inode_change_is_reported_without_deletion(self):
        driver = self.root / "driver-inode.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        src = self.make_incoming()
        home, skills, target = self.make_target(version=None)
        subprocess.run([sys.executable, str(driver),
                        str(src / "scripts" / "gg_update.py"), "adopt", "rn2",
                        str(src), str(target)], cwd=str(self.root),
                       capture_output=True, check=True)
        state = home / "knuaf-doc-update"
        journal = json.loads((state / "journal.json").read_text(encoding="utf-8"))
        staging = Path(journal["staging"])
        old_staging = skills / "old-staging"
        staging.rename(old_staging)
        staging.mkdir()
        (staging / "sentinel").write_text("preserve", encoding="utf-8")
        helper = next((state / "bin").glob("gg_update-*.py"))
        result = subprocess.run([sys.executable, str(helper), "recover",
                                 "--home", str(home), "--confirm"],
                                cwd=str(self.root), capture_output=True,
                                text=True, check=True)
        out = json.loads(result.stdout)
        self.assertEqual("completed", out["status"])
        self.assertEqual("skipped", out["cleanup"])
        self.assertEqual("preserve", (staging / "sentinel").read_text(
            encoding="utf-8"))

    def test_git_archive_zip_with_directory_entries_applies(self):
        repo = self.tmpdir("git-archive-fixture")
        tree = _write_skill(repo / "skills" / "knuaf-doc", version="0.1.2",
                            updater=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True,
                       capture_output=True)
        subprocess.run(["git", "add", "skills/knuaf-doc"], cwd=str(repo),
                       check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "commit", "-qm",
                        "fixture"], cwd=str(repo), check=True,
                       capture_output=True)
        data = subprocess.run(["git", "archive", "--format=zip",
                               "--prefix=fixture-0.1.2/", "HEAD"],
                              cwd=str(repo), check=True, capture_output=True).stdout
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertIn("fixture-0.1.2/", zf.namelist())
            self.assertIn("fixture-0.1.2/skills/knuaf-doc/", zf.namelist())
        _, _, target = self.make_target(updater=True)
        mod, out = self._apply(target, payload=data)
        self.assertEqual("applied", out["status"], out)
        self.assertEqual((0, 1, 2), mod.read_version_file(target / "version.json"))

    def test_two_processes_only_one_adopts(self):
        driver = self.root / "barrier.py"
        driver.write_text(_BARRIER_DRIVER, encoding="utf-8")
        src = self.make_incoming()
        home, _, target = self.make_target(version=None)
        state = home / "knuaf-doc-update"
        ready, go = self.root / "adopt.ready", self.root / "adopt.go"
        first = subprocess.Popen([sys.executable, str(driver),
            str(src / "scripts" / "gg_update.py"), "adopt", str(home),
            str(src), str(target), str(ready), str(go)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            _wait_signal(ready, first)
            before = (state / "journal.json").read_bytes()
            mod = _load_update(src / "scripts" / "gg_update.py")
            self.assertEqual("locked", mod.adopt_cmd(src, target, confirm=True)["reason"])
            self.assertEqual(before, (state / "journal.json").read_bytes())
            self.assertTrue((state / "lock").is_file())
            go.write_text("go")
            stdout, stderr = first.communicate(timeout=10)
            self.assertEqual(0, first.returncode, stderr)
            self.assertEqual("applied", json.loads(stdout)["status"])
        finally:
            if first.poll() is None:
                first.kill(); first.communicate()

    def test_unlock_is_usage_error_and_symlink_lock_is_refused(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target()
        cli = subprocess.run([sys.executable, str(src / "scripts" / "gg_update.py"),
                              "unlock", "--home", str(home), "--confirm"],
                             capture_output=True, text=True)
        self.assertEqual(2, cli.returncode)
        self.assertEqual({"status": "error", "reason": "usage_error"},
                         json.loads(cli.stdout))
        state = home / mod.STATE_DIR_NAME
        state.mkdir()
        outside = self.tmpdir("student-work") / "lock"
        outside.write_bytes(b"outside")
        (state / "lock").symlink_to(outside)
        before = _tree_bytes(target)
        self.assertEqual("unsafe_state_path", mod.adopt_cmd(
            src, target, confirm=True)["reason"])
        self.assertEqual(before, _tree_bytes(target))
        self.assertEqual(b"outside", outside.read_bytes())

    def test_recover_preserves_foreign_staging_leaf(self):
        src = self.make_incoming()
        driver = self.root / "leaf-driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        for kind in ("other_version", "bad_version", "file", "link"):
            with self.subTest(kind=kind):
                home, _, target = self.make_target(version=None)
                subprocess.run([sys.executable, str(driver),
                                str(src / "scripts" / "gg_update.py"),
                                "adopt", "rn2", str(src), str(target)], check=True)
                state = home / "knuaf-doc-update"
                journal = state / "journal.json"
                leaf = Path(json.loads(journal.read_text())["staging"]) / "knuaf-doc"
                if kind == "file":
                    leaf.write_bytes(b"foreign")
                elif kind == "link":
                    leaf.symlink_to(target, target_is_directory=True)
                else:
                    _write_skill(leaf, version="9.9.9")
                    if kind == "bad_version":
                        (leaf / "version.json").write_bytes(b"\xff")
                mod = _load_update(src / "scripts" / "gg_update.py")
                before = (journal.read_bytes(), _tree_bytes(state), _tree_bytes(target))
                self.assertEqual("manual_required", mod.recover_cmd(home, confirm=True)["status"])
                self.assertEqual(before, (journal.read_bytes(), _tree_bytes(state),
                                          _tree_bytes(target)))
                self.assertTrue((state / "lock").exists())
                self.assertTrue(os.path.lexists(str(leaf)))

    def test_zip_original_name_nul_rejected(self):
        top = "knuaf-doc-0.1.2/skills/knuaf-doc/"
        buf = io.BytesIO(_good_zip())
        with zipfile.ZipFile(buf, "a") as zf:
            zf.writestr(top + "nulXignored", b"payload")
        data = buf.getvalue().replace(b"nulXignored", b"nul\x00ignored")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertIn(NUL, zf.infolist()[-1].orig_filename)
        _, _, target = self.make_target(updater=True)
        before = _tree_bytes(target)
        out = self._apply(target, payload=data)[1]
        self.assertEqual(("refused", "zip_invalid", "bad_chars"),
                         (out["status"], out["reason"], out["detail"]))
        self.assertEqual(before, _tree_bytes(target))

    def test_recover_command_quotes_special_home(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home = self.root / "공백 $; 'home'"
        target = _write_skill(home / "skills" / "knuaf-doc", version=None)
        before = _tree_bytes(target)
        original, calls = mod._rename, [0]
        def faulty(a, b):
            calls[0] += 1
            if calls[0] in (2, 3):
                raise OSError("injected")
            return original(a, b)
        with mock.patch.object(mod, "_rename", side_effect=faulty):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("backup_preserved_target_missing", out["state"])
        self.assertEqual(out["recover_argv"], shlex.split(out["recover"]))
        proc = subprocess.run(shlex.split(out["recover"]), capture_output=True,
                              text=True, check=True)
        self.assertEqual("recovered_old", json.loads(proc.stdout)["status"])
        self.assertEqual(before, _tree_bytes(target))

    def test_version_bad_encoding_and_permission(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target()
        before = _tree_bytes(target)
        (target / "version.json").write_bytes(b"\xff")
        cli = subprocess.run([sys.executable, str(src / "scripts" / "gg_update.py"),
                              "adopt", "--source", str(src), "--target",
                              str(target), "--confirm"], capture_output=True, text=True)
        self.assertEqual(0, cli.returncode)
        self.assertEqual("target_version_invalid", json.loads(cli.stdout)["reason"])
        self.assertEqual(1, len(cli.stdout.splitlines()))
        self.assertNotIn("Traceback", cli.stderr)
        self.assertEqual(b"\xff", (target / "version.json").read_bytes())
        (target / "version.json").write_text(json.dumps(_version_doc("0.1.1")))
        read = mod.read_version_file
        with mock.patch.object(mod, "read_version_file", side_effect=lambda p:
                               (_ for _ in ()).throw(PermissionError()) if
                               Path(p) == target / "version.json" else read(p)):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("target_version_invalid", out["reason"])
        self.assertEqual(before, _tree_bytes(target))
        report = _load_module(SCRIPTS_DIR / "gg_report.py", "report")
        with mock.patch.object(report.Path, "read_text", side_effect=PermissionError()):
            self.assertIsNone(report._read_version_json(target / "version.json"))

    def test_backup_collision_lock_unsupported_and_rollback_cut(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target()
        before = _tree_bytes(target)
        with mock.patch.object(mod, "_os_lock", side_effect=OSError(37, "unsupported")):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("lock_unsupported", out["reason"])
        self.assertEqual(before, _tree_bytes(target))
        self.assertTrue((home / "knuaf-doc-update" / "lock").is_file())
        state = home / "knuaf-doc-update"
        collision = state / "backups" / "0.1.1-20260101T000000Z-bbbbbbbb"
        collision.mkdir(parents=True)
        (collision / "sentinel").write_bytes(b"keep")
        with mock.patch.object(mod, "_utc_stamp", return_value="20260101T000000Z"), \
             mock.patch.object(mod, "_nonce", side_effect=["a" * 16, "b" * 16, "c" * 16]):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("applied", out["status"])
        self.assertEqual(b"keep", (collision / "sentinel").read_bytes())
        driver = self.root / "rollback-driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        home2, _, target2 = self.make_target(version=None)
        old = _tree_bytes(target2)
        subprocess.run([sys.executable, str(driver),
                        str(src / "scripts" / "gg_update.py"), "adopt", "rbk",
                        str(src), str(target2)], check=True)
        state2 = home2 / "knuaf-doc-update"
        helper = next((state2 / "bin").glob("gg_update-*.py"))
        proc = subprocess.run([sys.executable, str(helper), "recover", "--home",
                               str(home2), "--confirm"], capture_output=True,
                              text=True, check=True)
        self.assertEqual("not_started", json.loads(proc.stdout)["status"])
        self.assertEqual(old, _tree_bytes(target2))
        self.assertFalse((state2 / "journal.json").exists())

    def test_location_supplement_23_rows(self):
        _, _, target = self.make_target()
        v = "/starceas/knuaf-doc/releases/tag/v1.2.3"
        rows = {"": "unknown", "https://github.com" + v: "update_available",
                v: "update_available", "tag/v1.2.3": "unknown",
                "https://GITHUB.COM" + v: "update_available",
                v.replace("starceas", "Starceas"): "unknown",
                v + "/": "unknown", "/starceas/knuaf-doc/releases/": "unknown",
                "/starceas/knuaf-doc/releases?x=1": "unknown",
                v.replace("v1", "%761"): "unknown",
                "https://evil.example" + v: "unknown",
                "//evil.example" + v: "unknown",
                "https://github.com@evil.example" + v: "unknown",
                "https://github.com:444" + v: "unknown",
                "https://github.com:bad" + v: "unknown",
                "https://github.com.evil" + v: "unknown",
                v[:-2]: "unknown", v + "-rc1": "unknown",
                v.replace("1", "١"): "unknown", v + BS: "unknown",
                v + NUL: "unknown", v + "\x7f": "unknown", v + "\t": "unknown"}
        self.assertEqual(23, len(rows))
        for loc, want in rows.items():
            with self.subTest(loc=repr(loc)):
                result = self.gu.check_status(local_root=target,
                    connect=_connect(302, [("Location", loc)]))
                self.assertEqual(want, result["status"], result)

    def test_recover_competes_with_apply_and_recover(self):
        driver = self.root / "barrier.py"
        driver.write_text(_BARRIER_DRIVER, encoding="utf-8")
        cut = self.root / "cut.py"
        cut.write_text(_DRIVER, encoding="utf-8")
        src = self.make_incoming()
        home, _, target = self.make_target(version=None)
        mod = _load_update(src / "scripts" / "gg_update.py")
        subprocess.run([sys.executable, str(cut),
            str(src / "scripts" / "gg_update.py"), "adopt", "wj1",
            str(src), str(target)], check=True, capture_output=True)
        state = home / "knuaf-doc-update"
        old = json.loads((state / "journal.json").read_text())
        ready, go = self.root / "recover.ready", self.root / "recover.go"
        first = subprocess.Popen([sys.executable, str(driver),
            str(src / "scripts" / "gg_update.py"), "recover", str(home),
            str(src), str(target), str(ready), str(go)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            held = _wait_signal(ready, first)
            self.assertEqual(old["txid"], held["txid"])
            before = (state / "journal.json").read_bytes()
            self.assertEqual("locked", mod.recover_cmd(home, confirm=True)["reason"])
            self.assertEqual("locked", mod.adopt_cmd(src, target, confirm=True)["reason"])
            self.assertEqual(before, (state / "journal.json").read_bytes())
            go.write_text("go")
            stdout, stderr = first.communicate(timeout=10)
            self.assertEqual(0, first.returncode, stderr)
            self.assertEqual("not_started", json.loads(stdout)["status"])
            closed = state / ("journal-%s-closed.json" % old["txid"])
            self.assertEqual(old["txid"], json.loads(closed.read_text())["txid"])
            newer = mod.adopt_cmd(src, target, confirm=True)
            self.assertEqual("applied", newer["status"])
            active = json.loads((state / "journal.json").read_text())
            self.assertNotEqual(old["txid"], active["txid"])
            self.assertEqual(old["txid"], json.loads(closed.read_text())["txid"])
        finally:
            if first.poll() is None:
                first.kill(); first.communicate()

    def test_lock_identity_barriers_and_foreign_journal(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        # (a) replacement before open: the new regular file is the lock.
        home, _, target = self.make_target(version=None)
        state = home / mod.STATE_DIR_NAME
        state.mkdir()
        lock = state / "lock"
        lock.write_bytes(b"old")
        lock.unlink(); lock.write_bytes(b"new")
        self.assertEqual("applied", mod.adopt_cmd(src, target, confirm=True)["status"])
        self.assertEqual(b"new", lock.read_bytes())
        # (b) open then replacement before first identity check.
        home, _, target = self.make_target(version=None)
        state = home / mod.STATE_DIR_NAME
        before = _tree_bytes(target)
        original = mod._os_lock
        def swap_after_open(fd):
            original(fd)
            lock = state / "lock"
            lock.unlink(); lock.write_bytes(b"foreign")
        with mock.patch.object(mod, "_os_lock", side_effect=swap_after_open):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("lock_replaced", out["reason"])
        self.assertEqual(before, _tree_bytes(target))
        self.assertFalse((state / "journal.json").exists())
        # (c) first identity passed, replacement before final check.
        home, _, target = self.make_target(version=None)
        state = home / mod.STATE_DIR_NAME
        state.mkdir()
        journal = state / "journal.json"
        journal.write_text(json.dumps({"schema": mod.JOURNAL_SCHEMA,
            "home": str(home), "txid": "f" * 16, "phase": "done"}))
        old_journal = journal.read_bytes()
        before = _tree_bytes(target)
        original_check = mod._check_lock_identity
        checks = [0]
        def swap_before_final(sd, fd):
            checks[0] += 1
            if checks[0] == 2:
                (sd / "lock").unlink(); (sd / "lock").write_bytes(b"foreign")
            return original_check(sd, fd)
        with mock.patch.object(mod, "_check_lock_identity", side_effect=swap_before_final):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("lock_replaced", out["reason"])
        self.assertEqual(2, checks[0])
        self.assertEqual(before, _tree_bytes(target))
        self.assertEqual(old_journal, journal.read_bytes())

    def test_lock_lifetime_reentry_failure_and_unsupported(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target(version=None)
        state = mod._prepare_state(target.parent, home)
        driver = self.root / "barrier.py"
        driver.write_text(_BARRIER_DRIVER, encoding="utf-8")
        ready, go = self.root / "external.ready", self.root / "external.go"
        fd = mod._acquire_lock(state)
        try:
            with self.assertRaises(mod.Refused) as caught:
                mod._acquire_lock(state)
            self.assertEqual("locked", caught.exception.reason)
            competitor = subprocess.run([sys.executable, str(driver),
                str(src / "scripts" / "gg_update.py"), "lock", str(home),
                str(src), str(target), str(ready), str(go)],
                capture_output=True, text=True)
            self.assertNotEqual(0, competitor.returncode)
            self.assertIn("locked", competitor.stderr)
        finally:
            mod._release_lock(fd)
        again = mod._acquire_lock(state); mod._release_lock(again)
        with mock.patch.object(mod, "_os_lock", side_effect=OSError(37, "unsupported")), \
             mock.patch.object(mod, "_read_journal", side_effect=AssertionError("read")):
            out = mod.adopt_cmd(src, target, confirm=True)
        self.assertEqual("lock_unsupported", out["reason"])
        again = mod._acquire_lock(state); mod._release_lock(again)
        with mock.patch.object(mod, "_read_journal", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                mod.adopt_cmd(src, target, confirm=True)
        again = mod._acquire_lock(state); mod._release_lock(again)

    @unittest.skipIf(os.name == "nt", "POSIX SIGKILL evidence")
    def test_killed_owner_with_live_subprocess_releases_lock(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        home, _, target = self.make_target(version=None)
        mod._prepare_state(target.parent, home)
        driver = self.root / "barrier.py"
        driver.write_text(_BARRIER_DRIVER, encoding="utf-8")
        ready, go = self.root / "parent.ready", self.root / "parent.go"
        parent = subprocess.Popen([sys.executable, str(driver),
            str(src / "scripts" / "gg_update.py"), "parent_with_child", str(home),
            str(src), str(target), str(ready), str(go)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            row = _wait_signal(ready, parent)
            child_ready = ready.with_name(ready.name + "-child")
            _wait_signal(child_ready, parent)
            parent.kill(); parent.wait(timeout=10)
            self.assertTrue(Path("/proc/%d" % row["child"]).exists()
                            if Path("/proc").exists() else
                            subprocess.run(["kill", "-0", str(row["child"])],
                                           capture_output=True).returncode == 0)
            fd = mod._acquire_lock(home / mod.STATE_DIR_NAME)
            mod._release_lock(fd)
        finally:
            go.write_text("go")
            if parent.poll() is None:
                parent.kill(); parent.wait(timeout=10)
            parent.stdout.close(); parent.stderr.close()

    def test_windows_locking_offsets_mock_only(self):
        src = self.make_incoming()
        mod = _load_update(src / "scripts" / "gg_update.py")
        import types
        fake = types.SimpleNamespace(LK_NBLCK=10, LK_UNLCK=11)
        calls = []
        fake.locking = lambda fd, op, size: calls.append(("locking", fd, op, size))
        with tempfile.TemporaryFile() as fh, \
             mock.patch.object(mod.os, "name", "nt"), \
             mock.patch.object(mod.os, "lseek", side_effect=lambda fd, off, whence:
                               calls.append(("seek", fd, off, whence))), \
             mock.patch.dict(sys.modules, {"msvcrt": fake}):
            fd = fh.fileno()
            mod._os_lock(fd); mod._os_unlock(fd)
        self.assertEqual([("seek", fd, 0, os.SEEK_SET),
                          ("locking", fd, 10, 1),
                          ("seek", fd, 0, os.SEEK_SET),
                          ("locking", fd, 11, 1)], calls)

    def test_skill_guidance_user_finish_status_mutants(self):
        raw = SKILL_MD_PATH.read_text(encoding="utf-8")
        clauses = (
            ("⚑로 올리지 않고 DOCX·XLSX 작업을 막지 않는다.",
             "⚑로 올리고 DOCX·XLSX 작업을 막는다."),
            ("status에서 이미 통과한 항목은 next에 남아 있어도 다시 미완료로 쓰지 않는다.",
             "next에 남은 항목은 status에서 이미 통과했어도 다시 미완료로 쓴다."),
        )
        for wanted, reversed_meaning in clauses:
            with self.subTest(wanted=wanted):
                self.assertEqual(1, raw.count(wanted))
                mutant = raw.replace(wanted, reversed_meaning, 1)
                with self.assertRaises(AssertionError):
                    self.assertIn(wanted, mutant)

    def test_report_draft_with_corrupt_version(self):
        root = self.tmpdir("draft-bad-version")
        skill = root / "skills" / "knuaf-doc"
        (skill / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPTS_DIR / "gg_report.py", skill / "scripts" / "gg_report.py")
        (skill / "version.json").write_bytes(b"\xff")
        request = root / "request.json"
        request.write_text(json.dumps({"kind": "issue", "category": "bug",
                                       "title": "test", "description": "test"}))
        output = root / "draft.json"
        result = subprocess.run([sys.executable, str(skill / "scripts" / "gg_report.py"),
                                 "draft", "--input", str(request), "--out",
                                 str(output)], cwd=str(root), capture_output=True,
                                text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(output.exists())
        self.assertNotIn("Traceback", result.stderr)
