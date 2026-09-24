#!/usr/bin/env python3
"""Generate only explicit, supported financial plans; never invented sample inputs."""

import argparse
import json
from pathlib import Path
from gg_core import local
from gg_finance import validate_economic_inputs, workbook


def build(base, input_path, out_path):
    """Load the spec, run the pre-save economic validation, then build.

    Draft-purpose validation: hard "fail" issues refuse the build and are
    returned as {"status": "blocked", "reason": ..., "issues": [...]};
    soft "blocked" provenance issues never refuse a draft — they ride
    along in the success result as "economic_validation_issues".
    """
    spec = json.loads(local(base, input_path).read_text(encoding="utf-8"))
    issues = validate_economic_inputs(spec, purpose="draft")
    if any(i["status"] == "fail" for i in issues):
        return {
            "status": "blocked",
            "reason": "경제 입력 검증 실패 — 생성 중단",
            "issues": issues,
        }
    result = workbook(spec, local(base, out_path))
    return dict(result, economic_validation_issues=issues)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base")
    ap.add_argument(
        "--input", required=True, help="근거·기간·단위를 가진 계산 계약 JSON"
    )
    ap.add_argument("--out", default="build/검토전_재무.xlsx")
    a = ap.parse_args()
    try:
        result = build(a.base, a.input, a.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") == "blocked":
            raise SystemExit(2)
    except (ValueError, KeyError, OSError) as e:
        print(json.dumps({"status": "blocked", "reason": str(e)}, ensure_ascii=False))
        raise SystemExit(2)
