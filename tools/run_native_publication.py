#!/usr/bin/env python3
"""Run the native Windows publication suite; zero skips or empty suites allowed."""
import argparse
import hashlib
import platform
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
MODULE = "tests.test_winpub_native"


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids = []

    def addSuccess(self, test):
        self.passed_ids.append(test.id())
        super().addSuccess(test)


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def native_volumes():
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = api.GetVolumeInformationW
    fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                   ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                   ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    fn.restype = wintypes.BOOL
    out = []
    for anchor in sorted({ROOT.anchor, Path(tempfile.gettempdir()).anchor}):
        name = ctypes.create_unicode_buffer(64)
        serial, max_component, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        ok = fn(anchor, None, 0, ctypes.byref(serial), ctypes.byref(max_component),
                ctypes.byref(flags), name, len(name))
        out.append({"drive": anchor, "filesystem": name.value if ok else None,
                    "winerror": None if ok else ctypes.get_last_error()})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    report = {
        "schema": "knuaf-doc/native-publication-validation@1",
        "platform": sys.platform, "os_name": os.name,
        "python": sys.version, "interpreter": sys.executable,
        "module": MODULE, "host": platform.platform(),
        "candidate_sha": os.environ.get("GITHUB_SHA"),
        "candidate_files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted((ROOT / "skills/knuaf-doc/scripts").glob("gg_*.py"))},
        "tests_run": 0, "skipped": [],
        "verdict": "not_run", "exit_code": 2,
    }
    if sys.platform != "win32" or os.name != "nt":
        report["reason"] = "native Windows required; emulation is not accepted"
    elif sys.version_info[:2] not in {(3, 10), (3, 14)}:
        report["reason"] = "native CI contract requires Python 3.10 or 3.14"
    else:
        report["volumes"] = native_volumes()
        sys.path.insert(0, str(ROOT))
        suite = unittest.defaultTestLoader.loadTestsFromName(MODULE)
        ids = list(test_ids(suite))
        result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
        clean = bool(
            ids and len(ids) == len(set(ids))
            and all(t.startswith(MODULE + ".") for t in ids)
            and result.testsRun == len(ids)
            and len(result.passed_ids) == len(ids)
            and result.wasSuccessful() and not result.skipped
            and not result.expectedFailures and not result.unexpectedSuccesses
        )
        report.update({
            "collected_ids": ids, "passed_ids": result.passed_ids,
            "tests_run": result.testsRun,
            "skipped": [{"test": t.id(), "reason": why} for t, why in result.skipped],
            "failed_ids": [t.id() for t, _ in result.failures],
            "error_ids": [t.id() for t, _ in result.errors],
            "expected_failure_ids": [t.id() for t, _ in result.expectedFailures],
            "unexpected_success_ids": [t.id() for t in result.unexpectedSuccesses],
            "verdict": "pass" if clean else "fail", "exit_code": 0 if clean else 1,
        })
    target = Path(args.json)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"python", "interpreter", "candidate_files"}}, ensure_ascii=False))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
