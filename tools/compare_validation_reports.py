#!/usr/bin/env python3
"""Compare full validation outcomes by test ID against a baseline artifact."""
import argparse
import json
from pathlib import Path
import sys


def _strict_load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("nonfinite JSON value: " + value)

    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=unique, parse_constant=reject_constant)


def _entries(report, label):
    runtime = report.get("runtime")
    entries = runtime.get("entries") if isinstance(runtime, dict) else None
    if not isinstance(entries, list):
        raise ValueError(label + " report has no runtime.entries list")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("test"), str):
            raise ValueError(label + " report contains a malformed test entry")
        test_id = entry["test"]
        if test_id in result:
            raise ValueError(label + " report has duplicate test ID: " + test_id)
        if not isinstance(entry.get("category"), str):
            raise ValueError(label + " report has an entry without a category: " + test_id)
        result[test_id] = entry
    if runtime.get("tests_run") != len(result):
        raise ValueError(label + " tests_run does not match unique test IDs")
    framework = runtime.get("accounting", {}).get("framework")
    if isinstance(framework, dict) and framework.get("testsRun") != len(result):
        raise ValueError(label + " framework testsRun does not match entries")
    return result


def compare(baseline, candidate):
    before = _entries(baseline, "baseline")
    after = _entries(candidate, "candidate")
    missing = sorted(before.keys() - after.keys())
    extra = sorted(after.keys() - before.keys())
    changed = []
    for test_id in sorted(before.keys() & after.keys()):
        old = before[test_id]
        new = after[test_id]
        old_result = (old["category"], old.get("detail"), old.get("defect"),
                      old.get("gate"), old.get("observed"), old.get("events"))
        new_result = (new["category"], new.get("detail"), new.get("defect"),
                      new.get("gate"), new.get("observed"), new.get("events"))
        if old_result != new_result:
            changed.append({"test": test_id, "baseline": old_result,
                            "candidate": new_result})
    return {"baseline_tests": len(before), "candidate_tests": len(after),
            "missing_test_ids": missing, "extra_test_ids": extra,
            "changed_outcomes": changed,
            "same_test_id_set": not missing and not extra,
            "same_test_outcomes": not missing and not extra and not changed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="main CI validation report JSON")
    parser.add_argument("candidate", help="merged candidate validation report JSON")
    parser.add_argument("--json", metavar="PATH", help="also write comparison JSON")
    args = parser.parse_args(argv)
    try:
        report = compare(_strict_load(args.baseline), _strict_load(args.candidate))
    except (OSError, ValueError, TypeError) as exc:
        print("comparison failed: " + str(exc), file=sys.stderr)
        return 2
    summary = json.dumps(report, ensure_ascii=False, indent=2)
    print(summary)
    if args.json:
        output = Path(args.json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(summary + "\n", encoding="utf-8")
    return 0 if report["same_test_outcomes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
