#!/usr/bin/env python3
"""Check deployment dispositions for a knuaf-doc repository tree.

Usage: python3 tools/check_bundle.py --root <public_root> [--json <path>]

Exit codes: 0 clean, 1 deployment findings, 2 usage/operator error.
"""

import argparse
import json
from pathlib import Path
import sys

import check_asset_disposition


def check(root):
    """Return the deployment report; nonempty errors mean a failed check."""
    errors = []
    for error in check_asset_disposition.check(root):
        code = "asset_disposition:" + error["code"]
        errors.append({"code": code, "path": error["path"],
                       "detail": f"{code}:{error['path']}"})
    return {
        "status": "errors" if errors else "ok",
        "errors": errors,
        "notes": [],
        "python": sys.version.split()[0],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, help="public repository tree root")
    parser.add_argument("--json", dest="json_out", help="write full report to this path")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: --root {root} is not a directory", file=sys.stderr)
        return 2
    report = check(root)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = {}
    for err in report["errors"]:
        counts[err["code"]] = counts.get(err["code"], 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
    print(f"check_bundle: status={report['status']} errors: {summary}")
    for err in report["errors"]:
        print(f"  {err['code']}: {err['path']} — {err['detail']}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
