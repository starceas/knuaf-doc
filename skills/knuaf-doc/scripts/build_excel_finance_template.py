#!/usr/bin/env python3
"""Generate only explicit, supported financial plans; never invented sample inputs."""

import argparse
import json
from pathlib import Path
from gg_core import local
from gg_finance import validate_economic_inputs, workbook


def build(base, input_path, out_path, *, major_id=None):
    """Load the spec, run the pre-save economic validation, then build.

    Draft-purpose validation: hard "fail" issues refuse the build and are
    returned as {"status": "blocked", "reason": ..., "issues": [...]};
    soft "blocked" provenance issues never refuse a draft — they ride
    along in the success result as "economic_validation_issues".
    """
    import gg_major_contract as mc

    context = mc.output_context(base, major_id)
    spec = json.loads(local(base, input_path).read_text(encoding="utf-8"))
    issues = validate_economic_inputs(spec, purpose="draft")
    if any(i["status"] == "fail" for i in issues):
        return {
            "status": "blocked",
            "reason": "경제 입력 검증 실패 — 생성 중단",
            "issues": issues,
        }
    result = workbook(spec, local(base, out_path), context=context)
    return dict(result, economic_validation_issues=issues)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base")
    ap.add_argument(
        "--input", required=True, help="근거·기간·단위를 가진 계산 계약 JSON"
    )
    ap.add_argument("--out", default="build/검토전_재무.xlsx")
    ap.add_argument("--major", default=None, help="명시 전공 ID")
    a = ap.parse_args()
    import gg_major_contract as mc
    try:
        result = build(a.base, a.input, a.out, major_id=a.major)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") == "blocked":
            raise SystemExit(2)
    except mc.OutputHeldError as e:
        print(json.dumps({
            "status": "held", "reason": e.reason, "detail": str(e),
            "guidance": e.detail.get("guidance"),
        }, ensure_ascii=False))
        raise SystemExit(2)
    except (ValueError, KeyError, OSError) as e:
        print(json.dumps({"status": "blocked", "reason": str(e)}, ensure_ascii=False))
        raise SystemExit(2)
