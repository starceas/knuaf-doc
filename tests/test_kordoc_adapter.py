"""gg_kordoc 어댑터 계약 시험 — kordoc 4.17.1 (W2, DESIGN §2).

가짜 node/pnpm/kordoc 실행파일(임시 폴더의 셸 스크립트)로 네트워크 없이
(a) Node 19 거부·20 허용, (b) 4.13.1 루트 표식 캐시에서 4.17.1 설치가
cache_unowned로 막히지 않음, (c) 버전 폴더 표식 불일치 거부,
(d) 외부 CLI 필수 플래그 누락 거부를 확인한다.
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

NODE_SCRIPT = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "v%s"
  exit 0
fi
exit 0
"""

PNPM_SCRIPT = """#!/bin/sh
if [ "$1" = "add" ]; then
  for last in "$@"; do :; done
  name="${last%%@*}"
  ver="${last##*@}"
  mkdir -p "node_modules/$name/dist"
  printf '{"name":"%s","version":"%s"}\\n' "$name" "$ver" \\
    > "node_modules/$name/package.json"
  printf '// fake kordoc cli\\n' > "node_modules/$name/dist/cli.js"
  exit 0
fi
if [ "$1" = "--version" ]; then
  echo "9.0.0"
  exit 0
fi
exit 0
"""

KORDOC_OK_SCRIPT = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "kordoc 4.99.0"
  exit 0
fi
if [ "$1" = "--help" ]; then
  echo "kordoc - document parser"
  echo "--format --keep-empty-cols --keep-empty-paragraphs --silent"
  exit 0
fi
exit 0
"""

KORDOC_NOFLAGS_SCRIPT = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "kordoc 4.99.0"
  exit 0
fi
if [ "$1" = "--help" ]; then
  echo "kordoc - document parser"
  echo "--format --silent"
  exit 0
fi
exit 0
"""

# -o 출력과 사이드카를 흉내내는 node. $1이 --version이 아니면 parse 모드.
# NESTED는 kordoc 4.17.1의 images/<stem>/ 배치, FLAT은 4.13.1의 납작한 배치.
NODE_PARSE_NESTED = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "v20.11.0"
  exit 0
fi
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
if [ -n "$out" ]; then
  name="${out##*/}"
  stem="${name%.*}"
  mkdir -p "images/$stem"
  printf 'fakeimage' > "images/$stem/image_001.bmp"
  printf '[{"name":"image_001.bmp"}]\\n' > "images/$stem/manifest.json"
  printf 'body ![](images/%s/image_001.bmp)\\n' "$stem" > "$out"
fi
exit 0
"""

NODE_PARSE_FLAT = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "v20.11.0"
  exit 0
fi
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
if [ -n "$out" ]; then
  mkdir -p images
  printf 'fakeimage' > "images/image_001.bmp"
  printf '[{"name":"image_001.bmp"}]\\n' > "images/manifest.json"
  printf 'body ![](image_001.bmp)\\n' > "$out"
fi
exit 0
"""


def _exe(path, text):
    path = Path(path)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


class KordocAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kordoc-adapter-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.cache = self.root / "cache"
        # PATH 끝에 최소 시스템 디렉터리만 둔다: 셸 스크립트의 mkdir 등이
        # 동작해야 하고, 사용자 PATH의 실제 kordoc은 검색되지 않아야 한다.
        self._path_patch = mock.patch.dict(
            os.environ, {"PATH": str(self.bin) + os.pathsep + "/usr/bin:/bin"})
        self._path_patch.start()
        self.addCleanup(self._path_patch.stop)

    def _node(self, version="20.11.0"):
        return _exe(self.bin / "node", NODE_SCRIPT % version)

    def _pnpm(self):
        return _exe(self.bin / "pnpm", PNPM_SCRIPT)

    def _root_marker(self, version):
        self.cache.mkdir(parents=True)
        (self.cache / ".gg-kordoc-cache.json").write_text(
            json.dumps({
                "owner": gk.OWNER,
                "package": gk.PACKAGE,
                "version": version,
                "registry": gk.REGISTRY,
                "created_at": 1700000000,
            }),
            encoding="utf-8",
        )

    def test_node19_rejected_node20_allowed(self):
        node19 = self._node("19.9.0")
        with self.assertRaises(gk.KordocBlocked) as ctx:
            gk.ensure(cache_dir=str(self.cache), node=node19,
                      pnpm=self._pnpm())
        self.assertEqual("needs_runtime", ctx.exception.reason)
        self.assertIn("node>=20", ctx.exception.details["missing"])
        self.assertFalse(self.cache.exists())

        fresh = self.root / "cache20"
        result = gk.ensure(cache_dir=str(fresh), node=self._node("20.11.0"),
                           pnpm=self._pnpm())
        self.assertEqual("ready", result["status"])
        self.assertEqual("installed", result["action"])
        self.assertEqual(gk.VERSION, result["version"])
        self.assertTrue((fresh / gk.VERSION /
                         "node_modules" / "kordoc" / "dist" / "cli.js").is_file())

    def test_old_version_root_marker_allows_new_install(self):
        # 4.13.1 시절에 쓰인 루트 표식(version 키가 옛 버전)과 옛 버전 폴더가
        # 있는 캐시에서 4.17.1 설치가 cache_unowned로 막히지 않는다.
        self._root_marker("4.13.1")
        old_dir = self.cache / "4.13.1"
        old_dir.mkdir()
        (old_dir / ".gg-kordoc.json").write_text(
            json.dumps({"owner": gk.OWNER, "package": gk.PACKAGE,
                        "version": "4.13.1", "registry": gk.REGISTRY}),
            encoding="utf-8",
        )
        result = gk.ensure(cache_dir=str(self.cache), node=self._node(),
                           pnpm=self._pnpm())
        self.assertEqual("ready", result["status"])
        self.assertEqual("installed", result["action"])
        self.assertEqual(str(self.cache.resolve()), result["cache_dir"])
        # 옛 버전 폴더는 지우지 않는다.
        self.assertTrue((old_dir / ".gg-kordoc.json").is_file())

    def test_version_folder_marker_mismatch_rejected(self):
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
            json.dumps({"name": "kordoc", "version": gk.VERSION}),
            encoding="utf-8",
        )
        (pkg / "dist" / "cli.js").write_text("// fake\n", encoding="utf-8")
        with self.assertRaises(gk.KordocBlocked) as ctx:
            gk.ensure(cache_dir=str(self.cache), node=self._node(),
                      pnpm=self._pnpm())
        self.assertEqual("cache_invalid", ctx.exception.reason)

    def test_external_cli_missing_required_flags_rejected(self):
        kordoc = _exe(self.bin / "kordoc", KORDOC_NOFLAGS_SCRIPT)
        result = gk.ensure(cache_dir=str(self.cache), node=self._node(),
                           pnpm=self._pnpm(), kordoc=kordoc)
        # 외부 후보는 거부되고 전용 캐시 설치로 내려간다.
        self.assertEqual("ready", result["status"])
        self.assertEqual("installed", result["action"])
        rejected = result["rejected_candidates"]
        self.assertEqual(1, len(rejected))
        self.assertEqual(["required_flags_missing"], rejected[0]["reasons"])
        self.assertNotEqual(str(Path(kordoc).resolve()), result["cli_path"])

    def test_external_cli_with_flags_reused_and_version_recorded(self):
        kordoc = _exe(self.bin / "kordoc", KORDOC_OK_SCRIPT)
        result = gk.ensure(cache_dir=str(self.cache), node=self._node(),
                           pnpm=self._pnpm(), kordoc=kordoc)
        self.assertEqual("ready", result["status"])
        self.assertEqual("reused_existing", result["action"])
        self.assertEqual(str(Path(kordoc).resolve()), result["cli_path"])
        self.assertEqual("4.99.0", result["actual_version"])
        self.assertEqual("external", result["source"])

    def _parse_fixture(self, node_script):
        src = self.root / "doc.hwp"
        src.write_text("fake hwp bytes", encoding="utf-8")
        out = self.root / "out" / "result.md"
        node = _exe(self.bin / "node", node_script)
        result = gk.parse(str(src), str(out), cache_dir=str(self.cache),
                          node=node, pnpm=self._pnpm())
        return src, out, result

    def test_parse_nested_sidecar_layout(self):
        # kordoc 4.17.1은 images/<출력 stem>/ 아래에 manifest와 이미지를 둔다.
        src, out, result = self._parse_fixture(NODE_PARSE_NESTED)
        self.assertEqual("ready", result["status"])
        self.assertEqual("parsed", result["action"])
        content = out.read_text(encoding="utf-8")
        match = re.search(r"\]\((gg-assets-[^)]+/image_001\.bmp)\)", content)
        self.assertIsNotNone(match)
        self.assertIn("/images/result/", match.group(1))
        self.assertTrue((out.parent / match.group(1)).is_file())

    def test_parse_flat_sidecar_layout(self):
        # kordoc 4.13.1은 납작한 images/manifest.json + bare 이름 링크를 쓴다.
        src, out, result = self._parse_fixture(NODE_PARSE_FLAT)
        self.assertEqual("ready", result["status"])
        self.assertEqual("parsed", result["action"])
        content = out.read_text(encoding="utf-8")
        match = re.search(r"\]\((gg-assets-[^)]+/image_001\.bmp)\)", content)
        self.assertIsNotNone(match)
        self.assertIn("/images/image_001.bmp", match.group(1))
        self.assertTrue((out.parent / match.group(1)).is_file())


if __name__ == "__main__":
    unittest.main()
