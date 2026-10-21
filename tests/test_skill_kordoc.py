"""gg_kordoc: the bootstrap that downloads and runs a third-party npm CLI.

Migrated from the GUI contract suite (cb72ae1), pinned to this tree's
package version.  The risky part is not parsing — it is that it fetches a
package and executes it on a student's machine.  These tests drive a *fake*
Kordoc CLI through the ``--kordoc`` flag: no npm, no network.
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, runtime

PROBE = '''import sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("kordoc 4.17.1")
    raise SystemExit(0)
if argv[:1] == ["--help"]:
    print("kordoc --format --silent --keep-empty-cols --keep-empty-paragraphs")
    raise SystemExit(0)
'''


def out_arg():
    return 'out = argv[argv.index("-o") + 1] if "-o" in argv else ""\n'


FAKE = PROBE + out_arg() + '''
if out:
    open(out, "w", encoding="utf-8").write("# 변환됨\\n\\n본문 한 줄\\n")
'''


def write_exe(path, body):
    """Write a fake CLI this platform can execute.

    ``body`` is Python: Windows has neither ``#!`` nor an exec bit, so the
    same fake is reached through a ``.cmd`` launcher there.
    """
    path = Path(path)
    impl = path.with_name(path.name + "-impl.py")
    impl.write_text(body, encoding="utf-8")
    if os.name == "nt":
        exe = path.with_name(path.name + ".cmd")
        exe.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, impl),
                       encoding="utf-8")
        return exe
    exe = path
    exe.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n'
                   % (sys.executable, impl), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


class KordocTests(ContractCase):
    def setUp(self):
        self.kd = runtime("gg_kordoc")
        tmp = tempfile.TemporaryDirectory(prefix="p1-kordoc-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.fake = write_exe(self.tmp / "kordoc", FAKE)
        self.document = self.tmp / "학교 지침.hwp"
        self.document.write_bytes(b"\xd0\xcf\x11\xe0 fake hwp payload\n")

    # --- the input document is never touched ------------------------------

    def test_a_parsed_document_is_left_byte_identical(self):
        before = self.document.read_bytes()
        self.kd.parse(self.document, self.tmp / "out" / "지침.md",
                      kordoc=str(self.fake))
        self.assertEqual(before, self.document.read_bytes())

    def test_the_output_lands_where_it_was_asked_for(self):
        out = self.tmp / "out" / "지침.md"
        self.kd.parse(self.document, out, kordoc=str(self.fake))
        self.assertTrue(out.is_file())
        self.assertIn("변환됨", out.read_text(encoding="utf-8"))

    def test_no_staging_directory_is_left_behind(self):
        out = self.tmp / "out" / "지침.md"
        self.kd.parse(self.document, out, kordoc=str(self.fake))
        self.assertEqual([], list(out.parent.glob(".gg-kordoc-*")))

    # --- refusals that protect the student's files ------------------------

    def test_an_existing_output_is_never_overwritten(self):
        out = self.tmp / "지침.md"
        out.write_text("기존 내용", encoding="utf-8")
        with self.assertRaises(self.kd.KordocBlocked) as caught:
            self.kd.parse(self.document, out, kordoc=str(self.fake))
        self.assertEqual("output_exists", caught.exception.reason)
        self.assertIn("never overwritten",
                      caught.exception.details["action"])
        self.assertEqual("기존 내용", out.read_text(encoding="utf-8"))

    def test_an_output_that_appears_during_the_parse_is_still_not_overwritten(self):
        racer = write_exe(self.tmp / "kordoc", PROBE + out_arg() + (
            'open(out, "w", encoding="utf-8").write("# 변환됨\\n")\n'
            'open(%r, "w", encoding="utf-8").write("끼어든 내용")\n'
            % str(self.tmp / "지침.md")))
        out = self.tmp / "지침.md"
        with self.assertRaises(self.kd.KordocBlocked) as caught:
            self.kd.parse(self.document, out, kordoc=str(racer))
        self.assertEqual("output_exists", caught.exception.reason)
        self.assertIn("appeared during parse",
                      caught.exception.details["action"])
        self.assertEqual("끼어든 내용", out.read_text(encoding="utf-8"))

    def test_writing_over_the_input_is_refused(self):
        with self.assertRaises(self.kd.KordocBlocked) as caught:
            self.kd.parse(self.document, self.document, kordoc=str(self.fake))
        self.assertIn("input_output_same", str(caught.exception))

    def test_a_missing_input_is_refused(self):
        with self.assertRaises(self.kd.KordocBlocked) as caught:
            self.kd.parse(self.tmp / "없는파일.hwp", self.tmp / "out.md",
                          kordoc=str(self.fake))
        self.assertIn("input_missing", str(caught.exception))

    def test_a_failing_cli_publishes_nothing(self):
        broken = write_exe(self.tmp / "kordoc",
                           PROBE + "raise SystemExit(3)\n")
        out = self.tmp / "out" / "지침.md"
        with self.assertRaises(self.kd.KordocBlocked):
            self.kd.parse(self.document, out, kordoc=str(broken))
        self.assertFalse(out.exists())

    def test_an_empty_result_is_not_published(self):
        silent = write_exe(self.tmp / "kordoc",
                           PROBE + "raise SystemExit(0)\n")
        out = self.tmp / "out" / "지침.md"
        with self.assertRaises(self.kd.KordocBlocked):
            self.kd.parse(self.document, out, kordoc=str(silent))
        self.assertFalse(out.exists())

    # --- an unusable CLI is rejected before anything runs -----------------

    @unittest.skipIf(os.name == "nt",
                     "Windows에는 실행 비트가 없어 os.access(X_OK)가 항상 참")
    def test_an_explicit_path_that_is_not_executable_is_refused(self):
        plain = self.tmp / "kordoc"
        plain.write_text("not executable", encoding="utf-8")
        out = self.tmp / "out.md"
        with self.assertRaises(self.kd.KordocBlocked):
            self.kd.parse(self.document, out, kordoc=str(plain))
        self.assertFalse(out.exists())

    def test_a_cli_that_is_not_kordoc_is_rejected(self):
        impostor = write_exe(self.tmp / "kordoc", 'print("pandoc 3.1.0")\n')
        with self.assertRaises(self.kd.KordocBlocked):
            self.kd.parse(self.document, self.tmp / "out.md",
                          kordoc=str(impostor))

    def test_a_cli_missing_a_required_parse_flag_is_rejected(self):
        partial = write_exe(self.tmp / "kordoc", '''import sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("kordoc 4.17.1")
elif argv[:1] == ["--help"]:
    print("kordoc --format --silent")
''')
        with self.assertRaises(self.kd.KordocBlocked):
            self.kd.parse(self.document, self.tmp / "out.md",
                          kordoc=str(partial))

    # --- credentials never reach the child process -----------------------

    def test_proxy_credentials_are_stripped(self):
        cases = [
            ("http://user:pass@proxy.example:3128",
             "http://proxy.example:3128"),
            ("user:pass@proxy.example:3128", "proxy.example:3128"),
            ("https://u:p@proxy.example/path@keep",
             "https://proxy.example/path@keep"),
            ("http://proxy.example:3128", "http://proxy.example:3128"),
            ("proxy.example", "proxy.example"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(expected,
                                 self.kd._strip_proxy_userinfo(raw))

    def test_the_child_environment_is_an_allowlist(self):
        with mock.patch.dict(os.environ, {
                "AWS_SECRET_ACCESS_KEY": "비밀",
                "HTTPS_PROXY": "https://user:pass@proxy.example:3128"}):
            env = self.kd._base_env()
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertEqual("https://proxy.example:3128", env["HTTPS_PROXY"])

    def test_parsing_declares_the_offline_contract(self):
        env = self.kd._env_for_external(offline=True)
        self.assertEqual("1", env["KORDOC_OFFLINE"])
        self.assertIsNone(
            self.kd._env_for_external(offline=False).get("KORDOC_OFFLINE"))

    # --- the cache is owned, not squatted ---------------------------------

    def test_an_empty_directory_is_claimed_with_a_marker(self):
        root = self.tmp / "cache"
        self.kd._claim_cache(root)
        marker = json.loads(
            (root / ".gg-kordoc-cache.json").read_text(encoding="utf-8"))
        self.assertEqual(self.kd.OWNER, marker["owner"])
        self.assertEqual(self.kd.PACKAGE, marker["package"])
        self.assertEqual(self.kd.VERSION, marker["version"])
        self.assertEqual(self.kd.REGISTRY, marker["registry"])

    def test_claiming_the_same_cache_twice_is_fine(self):
        root = self.tmp / "cache"
        self.kd._claim_cache(root)
        self.kd._claim_cache(root)

    def test_a_populated_directory_is_never_taken_over(self):
        root = self.tmp / "내 문서"
        root.mkdir()
        (root / "논문.hwp").write_text("사용자 파일", encoding="utf-8")
        with self.assertRaises(self.kd.KordocBlocked) as caught:
            self.kd._claim_cache(root)
        self.assertIn("cache_unowned", str(caught.exception))
        self.assertEqual("사용자 파일",
                         (root / "논문.hwp").read_text(encoding="utf-8"))

    def test_a_cache_claimed_by_something_else_is_refused(self):
        markers = [
            {"owner": "someone.else", "package": "kordoc",
             "version": "4.17.1", "registry": "https://registry.npmjs.org"},
            {"owner": "ginseng-goat.gg_kordoc", "package": "other",
             "version": "4.17.1", "registry": "https://registry.npmjs.org"},
            {"owner": "ginseng-goat.gg_kordoc", "package": "kordoc",
             "version": "4.17.1", "registry": "http://evil.example"},
        ]
        for marker in markers:
            with self.subTest(marker=marker):
                root = self.tmp / "cache"
                root.mkdir(exist_ok=True)
                (root / ".gg-kordoc-cache.json").write_text(
                    json.dumps(marker), encoding="utf-8")
                with self.assertRaises(self.kd.KordocBlocked) as caught:
                    self.kd._claim_cache(root)
                self.assertIn("cache_unowned", str(caught.exception))

    def test_the_cache_root_is_shared_across_versions(self):
        """Each release lives in its own versioned subdirectory, so a marker
        written by another version still owns the root."""
        root = self.tmp / "cache"
        root.mkdir()
        (root / ".gg-kordoc-cache.json").write_text(json.dumps(
            {"owner": self.kd.OWNER, "package": self.kd.PACKAGE,
             "version": "0.0.1", "registry": self.kd.REGISTRY}),
            encoding="utf-8")
        self.kd._claim_cache(root)

    # --- the pinned version is a decision, not an accident ----------------

    def test_the_package_version_is_pinned_and_the_registry_is_public(self):
        self.assertEqual("4.17.1", self.kd.VERSION)
        self.assertEqual("https://registry.npmjs.org", self.kd.REGISTRY)

    def test_every_subprocess_call_disables_the_shell(self):
        source = Path(self.kd.__file__).read_text(encoding="utf-8")
        self.assertIn("shell=False", source)
        self.assertNotIn("shell=True", source)
        self.assertEqual(1, source.count("subprocess.run("))


if __name__ == "__main__":
    unittest.main()
