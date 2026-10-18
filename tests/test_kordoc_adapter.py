"""gg_kordoc 어댑터 계약 시험 — kordoc 4.17.1 (W2).

외부 프로세스(node/pnpm/kordoc)는 ``gg_kordoc._run``과 ``shutil.which``를
monkeypatch한 인-프로세스 가짜로 대체한다. 셸 스크립트 실행 파일을 쓰지 않으므로
macOS와 Windows CI에서 모두 돈다. 여기서 확인하는 계약은
(a) Node 19 거부·20 허용, (b) 4.13.1 루트 표식 캐시에서 4.17.1 설치가
cache_unowned로 막히지 않음, (c) 버전 폴더 표식 불일치 거부,
(d) 외부 CLI 필수 플래그 누락 거부, (e) need_generate가 generate 부명령과
핵심 옵션을 요구함, (f) Windows .cmd 셔임을 [node, cli.js]로 해석하거나
blocked로 닫음, (g) 하드링크 불가 파일시스템에서도 무덮어쓰기 발행.
"""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
from unittest import mock

from tests._harness import runtime

gk = runtime("gg_kordoc")

PARSER_HELP = (
    "kordoc - document parser\n"
    "--format --keep-empty-cols --keep-empty-paragraphs --silent\n"
)
GENERATE_HELP = (
    "Usage: kordoc generate|gen [options] <markdown>\n"
    "--preset --font --pt --org --toc --cover --plain --paper --image-dir --silent\n"
)


def _fake_exe(path):
    """바이트가 필요 없는 가짜 실행 파일. 셸 스크립트가 아니라 빈 파일이다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


class FakeRuntime:
    """``gg_kordoc._run``을 대신하는 인-프로세스 외부 프로세스 대역."""

    def __init__(self, *, node, parse_help=PARSER_HELP, generate_help=GENERATE_HELP,
                 generate_rc=0, external_generate_help=None, external_generate_rc=None,
                 node_version="v20.11.0", cli_version="kordoc 4.99.0",
                 sidecar_layout="nested"):
        self.node = node
        self.parse_help = parse_help
        self.generate_help = generate_help
        self.generate_rc = generate_rc
        # 외부 CLI와 전용 캐시 설치본의 generate 능력을 따로 흉내낸다.
        self.external_generate_help = external_generate_help
        self.external_generate_rc = external_generate_rc
        self.node_version = node_version
        self.cli_version = cli_version
        self.sidecar_layout = sidecar_layout
        self.calls = []

    @staticmethod
    def _ok(stdout=""):
        return {"ok": True, "returncode": 0, "stdout": stdout, "stderr": ""}

    def run(self, cmd, *, cwd=None, env=None, timeout=None):
        cmd = list(cmd)
        self.calls.append((cmd, str(cwd) if cwd else None))
        external = cmd[0] != self.node
        if cmd[0] == self.node and cmd[1:] == ["--version"]:
            return self._ok(self.node_version)
        if cmd[-1] == "--version":
            return self._ok(self.cli_version)
        if cmd[-1] == "--help":
            if "generate" in cmd:
                rc = (self.external_generate_rc if external else self.generate_rc) or 0
                if rc != 0:
                    return {"ok": False, "returncode": rc,
                            "stdout": "", "stderr": "generate help failed"}
                text = (self.external_generate_help if external
                        else self.generate_help)
                if text is None:
                    text = self.generate_help
                return self._ok(text)
            return self._ok(self.parse_help)
        if "add" in cmd:
            return self._install(cwd)
        if "-o" in cmd:
            return self._parse(cwd, cmd)
        raise AssertionError(f"unexpected command: {cmd!r}")

    def generate_calls(self):
        return [c for c, _ in self.calls if "generate" in c]

    def _install(self, cwd):
        pkg = Path(cwd) / "node_modules" / gk.PACKAGE
        (pkg / "dist").mkdir(parents=True, exist_ok=True)
        (pkg / "package.json").write_text(
            json.dumps({"name": gk.PACKAGE, "version": gk.VERSION}), encoding="utf-8")
        (pkg / "dist" / "cli.js").write_text("// fake kordoc cli\n", encoding="utf-8")
        return self._ok()

    def _parse(self, cwd, cmd):
        out = Path(cmd[cmd.index("-o") + 1])
        stem = out.stem
        if self.sidecar_layout == "nested":
            images = Path(cwd) / "images" / stem
            body = f"body ![](images/{stem}/image_001.bmp)\n"
        else:
            images = Path(cwd) / "images"
            body = "body ![](image_001.bmp)\n"
        images.mkdir(parents=True, exist_ok=True)
        (images / "image_001.bmp").write_bytes(b"fakeimage")
        (images / "manifest.json").write_text('[{"name":"image_001.bmp"}]', encoding="utf-8")
        out.write_text(body, encoding="utf-8")
        return self._ok()


class KordocAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kordoc-adapter-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.cache = self.root / "cache"
        self.node = _fake_exe(self.bin / "node")
        self.pnpm = _fake_exe(self.bin / "pnpm")
        self.which_map = {"node": self.node, "pnpm": self.pnpm}
        self.fake = FakeRuntime(node=self.node)

    def _activate(self, **overrides):
        for key, value in overrides.items():
            setattr(self.fake, key, value)
        run_patch = mock.patch.object(gk, "_run", side_effect=self.fake.run)
        which_patch = mock.patch.object(
            gk.shutil, "which", side_effect=lambda name: self.which_map.get(name))
        run_patch.start()
        which_patch.start()
        self.addCleanup(run_patch.stop)
        self.addCleanup(which_patch.stop)
        return self.fake

    def _external_kordoc(self):
        return _fake_exe(self.bin / "kordoc")

    def _shim(self, name="kordoc.cmd"):
        """Windows 셔임을 흉내내는 실행 비트 있는 가짜 파일."""
        return _fake_exe(self.bin / name)

    def _root_marker(self, version):
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / ".gg-kordoc-cache.json").write_text(
            json.dumps({"owner": gk.OWNER, "package": gk.PACKAGE,
                        "version": version, "registry": gk.REGISTRY,
                        "created_at": 1700000000}),
            encoding="utf-8",
        )

    # ---- Node floor -------------------------------------------------------

    def test_node19_rejected_node20_allowed(self):
        self._activate(node_version="v19.9.0")
        with self.assertRaises(gk.KordocBlocked) as ctx:
            gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm)
        self.assertEqual("needs_runtime", ctx.exception.reason)
        self.assertIn("node>=20", ctx.exception.details["missing"])
        self.assertFalse(self.cache.exists())

        fresh = self.root / "cache20"
        self.fake.node_version = "v20.11.0"
        result = gk.ensure(cache_dir=str(fresh), pnpm=self.pnpm)
        self.assertEqual("ready", result["status"])
        self.assertEqual("installed", result["action"])
        self.assertEqual(gk.VERSION, result["version"])
        self.assertTrue((fresh / gk.VERSION / "node_modules" / "kordoc"
                         / "dist" / "cli.js").is_file())

    # ---- Cache ownership --------------------------------------------------

    def test_old_version_root_marker_allows_new_install(self):
        # 4.13.1 시절 루트 표식과 옛 버전 폴더가 있어도 4.17.1 설치가 막히지 않는다.
        self._activate()
        self._root_marker("4.13.1")
        old_dir = self.cache / "4.13.1"
        old_dir.mkdir()
        (old_dir / ".gg-kordoc.json").write_text(
            json.dumps({"owner": gk.OWNER, "package": gk.PACKAGE,
                        "version": "4.13.1", "registry": gk.REGISTRY}),
            encoding="utf-8",
        )
        result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm)
        self.assertEqual("ready", result["status"])
        self.assertEqual("installed", result["action"])
        self.assertEqual(str(self.cache.resolve()), result["cache_dir"])
        self.assertTrue((old_dir / ".gg-kordoc.json").is_file())

    def test_version_folder_marker_mismatch_rejected(self):
        self._activate()
        self._root_marker(gk.VERSION)
        version_root = self.cache / gk.VERSION
        version_root.mkdir()
        (version_root / ".gg-kordoc.json").write_text(
            json.dumps({"owner": gk.OWNER, "package": gk.PACKAGE,
                        "version": "4.13.1", "registry": gk.REGISTRY}),
            encoding="utf-8",
        )
        pkg = version_root / "node_modules" / "kordoc"
        (pkg / "dist").mkdir(parents=True)
        (pkg / "package.json").write_text(
            json.dumps({"name": "kordoc", "version": gk.VERSION}), encoding="utf-8")
        (pkg / "dist" / "cli.js").write_text("// fake\n", encoding="utf-8")
        with self.assertRaises(gk.KordocBlocked) as ctx:
            gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm)
        self.assertEqual("cache_invalid", ctx.exception.reason)

    # ---- External reuse ---------------------------------------------------

    def test_external_cli_missing_required_flags_rejected(self):
        self._activate(parse_help="kordoc - document parser\n--format --silent\n")
        kordoc = self._external_kordoc()
        result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm, kordoc=kordoc)
        self.assertEqual("ready", result["status"])
        self.assertEqual("installed", result["action"])
        rejected = result["rejected_candidates"]
        self.assertEqual(1, len(rejected))
        self.assertEqual(["required_flags_missing"], rejected[0]["reasons"])

    def test_external_cli_with_flags_reused_and_version_recorded(self):
        self._activate()
        kordoc = self._external_kordoc()
        result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm, kordoc=kordoc)
        self.assertEqual("ready", result["status"])
        self.assertEqual("reused_existing", result["action"])
        self.assertEqual(str(Path(kordoc).resolve()), result["cli_path"])
        self.assertEqual("4.99.0", result["actual_version"])
        self.assertEqual("external", result["source"])

    # ---- generate capability ---------------------------------------------

    def test_need_generate_reused_when_generate_present(self):
        self._activate()
        kordoc = self._external_kordoc()
        result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm,
                           kordoc=kordoc, need_generate=True)
        self.assertEqual("reused_existing", result["action"])
        self.assertEqual("ok", result["capability_verification"]["generate"])
        self.assertEqual(list(gk.REQUIRED_GENERATE_FLAGS),
                         result["capability_verification"]["required_generate_flags"])

    def test_need_generate_missing_flag_falls_through_to_install(self):
        self._activate(external_generate_help="--preset --paper --silent\n")  # no --plain/--image-dir
        kordoc = self._external_kordoc()
        result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm,
                           kordoc=kordoc, need_generate=True)
        self.assertEqual("installed", result["action"])
        self.assertEqual("ok", result["capability_verification"]["generate"])
        self.assertEqual(["generate_flags_missing"],
                         result["rejected_candidates"][0]["reasons"])

    def test_need_generate_probe_failure_falls_through_to_install(self):
        self._activate(external_generate_rc=1)
        kordoc = self._external_kordoc()
        result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm,
                           kordoc=kordoc, need_generate=True)
        self.assertEqual("installed", result["action"])
        self.assertEqual(["generate_unavailable"],
                         result["rejected_candidates"][0]["reasons"])

    def test_need_generate_cache_install_validates_and_records(self):
        self._activate()
        result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm, need_generate=True)
        self.assertEqual("installed", result["action"])
        self.assertEqual("ok", result["capability_verification"]["generate"])
        self.assertTrue(self.fake.generate_calls())

    def test_need_generate_default_off_does_not_probe_generate(self):
        self._activate()
        kordoc = self._external_kordoc()
        result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm, kordoc=kordoc)
        self.assertEqual("reused_existing", result["action"])
        self.assertNotIn("generate", result["capability_verification"])
        self.assertEqual([], self.fake.generate_calls())

    # ---- Windows shim handling -------------------------------------------

    def test_is_windows_shim_suffix_and_platform(self):
        self.assertTrue(gk._is_windows_shim(Path("kordoc.CMD"), "win32"))
        self.assertTrue(gk._is_windows_shim(Path("kordoc.bat"), "win32"))
        self.assertFalse(gk._is_windows_shim(Path("kordoc.cmd"), "darwin"))
        self.assertFalse(gk._is_windows_shim(Path("kordoc.exe"), "win32"))

    def test_sibling_cli_js_finds_pnpm_global_layout(self):
        cli = self.bin / "global" / "5" / "node_modules" / "kordoc" / "dist" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("// real\n", encoding="utf-8")
        shim = self.bin / "kordoc.cmd"
        shim.write_text("@echo off\n", encoding="utf-8")
        self.assertEqual(cli, gk._sibling_cli_js(shim))

    def test_windows_cmd_shim_resolved_to_node_cli(self):
        self._activate()
        shim = self._shim()
        cli = self.bin / "node_modules" / "kordoc" / "dist" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("// real\n", encoding="utf-8")
        with mock.patch.object(gk.sys, "platform", "win32"):
            result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm,
                               kordoc=shim)
        self.assertEqual("reused_existing", result["action"])
        self.assertEqual([self.node, str(cli.resolve())], result["cli_command"])
        self.assertTrue(result["shim"])
        # 실행 명령이 셔임이 아니라 node+cli.js여야 한다.
        self.assertNotIn(shim, result["cli_command"])

    def test_windows_path_cmd_discovered_by_which_is_resolved(self):
        # shutil.which가 PATHEXT를 따라 .cmd를 돌려준 상황을 흉내낸다.
        self._activate()
        shim = self._shim()
        self.which_map["kordoc"] = shim
        cli = self.bin / "node_modules" / "kordoc" / "dist" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("// real\n", encoding="utf-8")
        with mock.patch.object(gk.sys, "platform", "win32"):
            result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm)
        self.assertEqual("reused_existing", result["action"])
        self.assertEqual([self.node, str(cli.resolve())], result["cli_command"])

    def test_windows_shim_without_cli_js_rejected(self):
        self._activate()
        shim = self._shim()
        with mock.patch.object(gk.sys, "platform", "win32"):
            result = gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm,
                               kordoc=shim)
        self.assertEqual("installed", result["action"])
        self.assertEqual(["windows_shim_unresolved"],
                         result["rejected_candidates"][0]["reasons"])

    def test_windows_shim_without_node_is_blocked(self):
        self._activate()
        self.which_map.pop("node", None)
        shim = self._shim()
        cli = self.bin / "node_modules" / "kordoc" / "dist" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("// real\n", encoding="utf-8")
        with mock.patch.object(gk.sys, "platform", "win32"):
            with self.assertRaises(gk.KordocBlocked) as ctx:
                gk.ensure(cache_dir=str(self.cache), pnpm=self.pnpm, kordoc=shim)
        self.assertEqual("needs_runtime", ctx.exception.reason)
        reasons = [r for c in ctx.exception.details["rejected_candidates"]
                   for r in c.get("reasons", [])]
        self.assertIn("windows_shim_needs_node", reasons)

    # ---- parse sidecar layouts -------------------------------------------

    def _parse_fixture(self, layout):
        self._activate(sidecar_layout=layout)
        src = self.root / "doc.hwp"
        src.write_text("fake hwp bytes", encoding="utf-8")
        out = self.root / "out" / "result.md"
        result = gk.parse(str(src), str(out), cache_dir=str(self.cache), pnpm=self.pnpm)
        return src, out, result

    def test_parse_nested_sidecar_layout(self):
        src, out, result = self._parse_fixture("nested")
        self.assertEqual("ready", result["status"])
        self.assertEqual("parsed", result["action"])
        content = out.read_text(encoding="utf-8")
        match = re.search(r"\]\((gg-assets-[^)]+/image_001\.bmp)\)", content)
        self.assertIsNotNone(match)
        self.assertIn("/images/result/", match.group(1))
        self.assertTrue((out.parent / match.group(1)).is_file())

    def test_parse_flat_sidecar_layout(self):
        src, out, result = self._parse_fixture("flat")
        self.assertEqual("ready", result["status"])
        self.assertEqual("parsed", result["action"])
        content = out.read_text(encoding="utf-8")
        match = re.search(r"\]\((gg-assets-[^)]+/image_001\.bmp)\)", content)
        self.assertIsNotNone(match)
        self.assertIn("/images/image_001.bmp", match.group(1))
        self.assertTrue((out.parent / match.group(1)).is_file())

    # ---- publish (hard-link + FAT/exFAT fallback) ------------------------

    def _stage(self, text="body\n"):
        stage = self.root / "stage.md"
        stage.write_text(text, encoding="utf-8")
        return stage

    def test_publish_hardlink_publishes_content(self):
        target = self.root / "out" / "result.md"
        target.parent.mkdir()
        stage = self._stage()
        gk._publish(stage, target)
        self.assertEqual("body\n", target.read_text(encoding="utf-8"))

    def test_publish_falls_back_when_link_unsupported(self):
        target = self.root / "out" / "result.md"
        target.parent.mkdir()
        stage = self._stage("copied body\n")
        with mock.patch("os.link", side_effect=OSError("link not supported")):
            gk._publish(stage, target)
        self.assertEqual("copied body\n", target.read_text(encoding="utf-8"))

    def test_publish_never_overwrites_existing_target(self):
        target = self.root / "out" / "result.md"
        target.parent.mkdir()
        target.write_text("existing\n", encoding="utf-8")
        stage = self._stage()
        with mock.patch("os.link", side_effect=OSError("link not supported")):
            with self.assertRaises(gk.KordocBlocked) as ctx:
                gk._publish(stage, target)
        self.assertEqual("output_exists", ctx.exception.reason)
        self.assertEqual("existing\n", target.read_text(encoding="utf-8"))

    def test_publish_maps_link_race_to_output_exists(self):
        target = self.root / "out" / "result.md"
        target.parent.mkdir()
        stage = self._stage()
        with mock.patch("os.link", side_effect=FileExistsError()):
            with self.assertRaises(gk.KordocBlocked) as ctx:
                gk._publish(stage, target)
        self.assertEqual("output_exists", ctx.exception.reason)
        self.assertFalse(target.exists())

    def test_parse_publishes_when_hardlink_unsupported(self):
        self._activate(sidecar_layout="flat")
        src = self.root / "doc.hwp"
        src.write_text("fake hwp bytes", encoding="utf-8")
        out = self.root / "out" / "result.md"
        with mock.patch("os.link", side_effect=OSError("link not supported")):
            result = gk.parse(str(src), str(out), cache_dir=str(self.cache),
                              pnpm=self.pnpm)
        self.assertEqual("parsed", result["action"])
        self.assertTrue(out.is_file())


class EnvAllowlistTests(unittest.TestCase):
    """외부 프로세스 환경 허용목록 계약 (W2b)."""

    def test_windows_path_vars_pass_but_tokens_are_withheld(self):
        environ = {
            # 비밀이 아닌 경로성 Windows 변수 — 그대로 통과해야 한다.
            "USERPROFILE": r"C:\Users\student",
            "HOMEDRIVE": "C:",
            "HOMEPATH": r"\Users\student",
            "SYSTEMDRIVE": "C:",
            "PROGRAMDATA": r"C:\ProgramData",
            "PROGRAMFILES": r"C:\Program Files",
            "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
            "COMMONPROGRAMFILES": r"C:\Program Files\Common Files",
            "PATH": r"C:\Windows\System32",
            # 비밀값류 — 계속 걸러져야 한다.
            "ANTHROPIC_API_KEY": "sk-secret",
            "GITHUB_TOKEN": "ghp_secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            # 프록시 자격증명은 값에서 제거되어야 한다.
            "HTTPS_PROXY": "http://user:pass@proxy.example:8080",
        }
        with mock.patch.dict(os.environ, environ, clear=True):
            env = gk._base_env()
        for key in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "SYSTEMDRIVE",
                    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
                    "COMMONPROGRAMFILES", "PATH"):
            self.assertIn(key, env)
        for key in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(key, env)
        self.assertEqual("http://proxy.example:8080", env["HTTPS_PROXY"])


if __name__ == "__main__":
    unittest.main()