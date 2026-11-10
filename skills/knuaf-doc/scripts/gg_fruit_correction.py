#!/usr/bin/env python3
"""과수 FY1 정정 사본 — 과수 전용 진입점 하나.

과수 16시트 학교 양식의 `9.경비계획` 21칸(연도칸 1 + 물가 지수 수식 20)만
바로잡은 **사본**을 만든다. 학교 원본과 공개 정정 엔진(`patch_copy`)은 그대로
두고, 저장소에 실린 정정 명세(`corrections/fy1-spec.json`, 해시 고정)만 읽는다.

진입점은 명세 경로를 인자로 받지 않는다. 명세 해시는 이 파일에도 상수로 박아
두어, 명세를 고치려면 코드도 함께 고쳐야 한다. 통과 조건은 다음 넷뿐이다.

1. 입력 사본의 16시트 수식 지문이 원본 프로필(`fruit_16_sheet_v1`)과 일치
2. 명세가 등록된 정정 프로필(`fruit_16_sheet_v1_fy1`)의 것과 일치
3. 엔진이 셀별 원문(현재 수식/값)을 대조해 21칸만 바꿈
4. 출력 사본의 지문이 정정 프로필과 일치

영수증에는 명세 경로·해시와 고친 칸 목록을 적는다. `gg_office`의 사슬 검사는
이 모양의 영수증만 프로필 전환으로 인정한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from gg_excel_formula_patch import MAP_SCHEMA, _json, _patch_copy
from gg_excel_template import sha256


SPEC_PATH = (Path(__file__).resolve().parents[1]
             / "references/fruit-trees/corrections/fy1-spec.json")
SPEC_RELATIVE = "references/fruit-trees/corrections/fy1-spec.json"
SPEC_SHA256 = "5e55ad3b5bbc9c8d57307156bd9ca56937517c7410f6ae735fae5ac1770f0c44"
BASE_PROFILE = "fruit_16_sheet_v1"
PROFILE = "fruit_16_sheet_v1_fy1"
BASE_SHA256 = "5f2896dd0c47d9b90a1ec3da30128ea0a9aa47f72aa79156a57bfd7bfd271374"
SHEET = "9.경비계획"
YEAR_CELL = "B12"
YEAR_VALUE = 2025
YEAR_FORMULA = "='12.손익계획'!C4"
INFLATION_CELLS = (
    "J15", "J16", "J34", "J35", "J53", "J54", "J72", "J73", "J91", "J92",
    "J110", "J111", "J129", "J130", "J148", "J149", "J167", "J168",
    "J186", "J187",
)
CELL_COUNT = len(INFLATION_CELLS) + 1


def _registered_entry(profile: str) -> dict:
    from gg_school_excel import WORKBOOK_REFERENCE_SET
    try:
        data = json.loads(Path(WORKBOOK_REFERENCE_SET).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("workbook reference set unreadable") from error
    for entry in data.get("workbook_profiles") or []:
        if isinstance(entry, dict) and entry.get("profile") == profile:
            return entry
    raise ValueError("workbook profile is not registered: " + profile)


def registered_cells(spec: dict) -> tuple[tuple[str, str, str], ...]:
    """(sheet, cell, new_formula) for the shipped correction, in spec order."""
    return tuple((row["sheet"], row["cell"], row["new_formula"])
                 for row in spec["patches"])


def _require_workbook_fingerprints(path: Path, profile: str, role: str) -> None:
    """Require the registered sheet form of one profile for a real workbook."""
    from gg_school_excel import formula_fingerprints, profile_structure

    expected = profile_structure(profile)
    if expected is None:
        raise ValueError("workbook profile is not registered: " + profile)
    from openpyxl import load_workbook

    try:
        book = load_workbook(path, read_only=True)
    except Exception as error:
        raise ValueError(f"{role} workbook unreadable: {error}") from error
    try:
        observed = list(book.sheetnames)
        if observed != expected["sheets"]:
            raise ValueError(
                f"{role} workbook does not carry the {profile} sheet form")
        fingerprints = formula_fingerprints(book)
    finally:
        book.close()
    changed = [name for name in observed
               if fingerprints[name] != expected["formula_fingerprints"][name]]
    if changed:
        raise ValueError(f"{role} workbook formula fingerprint does not match "
                         f"{profile}: {', '.join(changed)}")


def load_spec() -> dict:
    """Read the shipped correction spec and prove it is the registered one.

    No path argument: the module constant and the reference set entry must
    both agree with the bytes on disk.
    """
    try:
        raw = SPEC_PATH.read_bytes()
    except OSError as error:
        raise ValueError("correction spec unreadable") from error
    observed = hashlib.sha256(raw).hexdigest()
    if observed != SPEC_SHA256:
        raise ValueError(
            f"correction spec is not the registered spec: {observed}")
    try:
        spec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("correction spec is not JSON") from error
    if not isinstance(spec, dict):
        raise ValueError("correction spec must be an object")
    if spec.get("schema") != MAP_SCHEMA:
        raise ValueError(f"correction spec schema must be {MAP_SCHEMA}")
    if spec.get("workbook_profile") != PROFILE:
        raise ValueError("correction spec does not name the corrected profile")
    if spec.get("base_profile") != BASE_PROFILE:
        raise ValueError("correction spec does not name its base profile")
    source = spec.get("source")
    if not isinstance(source, dict) or source.get("binding") != "cells":
        raise ValueError("correction spec must declare source.binding=cells")
    if source.get("sha256") != BASE_SHA256:
        raise ValueError("correction spec does not name the registered base workbook")
    patches = spec.get("patches")
    if not isinstance(patches, list) or len(patches) != CELL_COUNT:
        raise ValueError(f"correction spec must carry exactly {CELL_COUNT} cells")
    seen: set[tuple[str, str]] = set()
    for row in patches:
        if not isinstance(row, dict):
            raise ValueError("correction spec cells must be objects")
        key = (row.get("sheet"), row.get("cell"))
        if key in seen:
            raise ValueError(f"duplicate correction cell: {key[0]}!{key[1]}")
        seen.add(key)
        if key[0] != SHEET:
            raise ValueError("correction spec cell outside the expense sheet")
    year = next((row for row in patches if row.get("cell") == YEAR_CELL), None)
    if (year is None or year.get("class") != "value_to_formula"
            or year.get("expected_value") != YEAR_VALUE
            or year.get("new_formula") != YEAR_FORMULA):
        raise ValueError("correction spec year cell is not the registered one")
    repointed = [row for row in patches if row.get("cell") != YEAR_CELL]
    if tuple(row.get("cell") for row in repointed) != INFLATION_CELLS:
        raise ValueError("correction spec inflation cells are not the registered 20")
    for row in repointed:
        if (row.get("class") != "formula_repoint"
                or not isinstance(row.get("expected_formula"), str)
                or "2020" not in row["expected_formula"]
                or not isinstance(row.get("new_formula"), str)
                or "$B$12" not in row["new_formula"]):
            raise ValueError(f"correction spec cell is not a 2020 repoint: "
                             f"{row.get('cell')}")
    entry = _registered_entry(PROFILE)
    correction = entry.get("correction_spec")
    if (entry.get("base_profile") != BASE_PROFILE
            or not isinstance(correction, dict)
            or correction.get("path") != SPEC_RELATIVE
            or correction.get("sha256") != SPEC_SHA256
            or correction.get("cells") != CELL_COUNT):
        raise ValueError("correction spec is not the profile's registered spec")
    if entry.get("sha256") != BASE_SHA256:
        raise ValueError("corrected profile does not name the registered base workbook")
    return spec


def _registered_map(spec: dict, source: Path) -> dict:
    """Derive the engine map: registered cells, this copy's own file hash.

    The engine binds cell text through the registered patches; the map's
    file hash only has to name the copy the engine is about to read.
    """
    derived = dict(spec)
    derived["source"] = dict(spec["source"], sha256=sha256(source))
    return derived


def _bare(formula: object) -> object:
    """The engine stores formulas without the leading '='; compare that form."""
    return formula[1:] if isinstance(formula, str) and formula.startswith("=") else formula


def _verify_patched(receipt: dict, spec: dict) -> None:
    expected = {(sheet, cell, _bare(formula))
                for sheet, cell, formula in registered_cells(spec)}
    observed = {(row.get("sheet"), row.get("cell"), _bare(row.get("new_formula")))
                for row in receipt.get("patched") or []}
    if observed != expected or len(receipt.get("patched") or []) != len(expected):
        raise ValueError("correction receipt does not record exactly the "
                         "registered cells")
    operations = sorted(row.get("operation") for row in receipt["patched"])
    if operations.count("value_to_formula") != 1:
        raise ValueError("correction receipt must record one value-to-formula cell")
    if operations.count("formula_replace") != len(INFLATION_CELLS):
        raise ValueError("correction receipt must record twenty formula repoints")


def apply_fy1(source, out, receipt, *, context=None) -> dict:
    """Correct a filled fruit copy; the original is never opened for writing."""
    import gg_major_contract as mc

    source, out, receipt_path = (Path(source).resolve(), Path(out).resolve(),
                                 Path(receipt).resolve())
    if len({source, out, receipt_path}) != 3:
        raise ValueError("source, out and receipt must be different paths")
    staged_out = out.with_name(f".{out.stem}.tmp-{os.getpid()}{out.suffix}")
    staged_receipt = receipt_path.with_name(
        f".{receipt_path.stem}.tmp-{os.getpid()}{receipt_path.suffix}")
    if out.exists() or receipt_path.exists() or staged_out.exists() or staged_receipt.exists():
        raise FileExistsError("refusing to overwrite output, receipt, or staging path")
    authorization = mc.authorize_output(mc.OUTPUT_SCHOOL_WORKBOOK, context)
    mc.require_workbook_profile(authorization, PROFILE)
    spec = load_spec()
    _require_workbook_fingerprints(source, BASE_PROFILE, "source")
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, derived_path = tempfile.mkstemp(
        dir=out.parent, prefix=f".{out.name}.correction-", suffix=".json")
    os.close(handle)
    derived_path = Path(derived_path)
    published = False
    try:
        derived_path.write_text(
            json.dumps(_registered_map(spec, source), ensure_ascii=False),
            encoding="utf-8")
        result = _patch_copy(source, derived_path, staged_out,
                             _authorization=authorization, _context=context)
        _require_workbook_fingerprints(staged_out, PROFILE, "corrected output")
        _verify_patched(result, spec)
        result["workbook_profile"] = PROFILE
        result["base_profile"] = BASE_PROFILE
        result["corrections"] = {
            "spec": {"path": SPEC_RELATIVE, "sha256": SPEC_SHA256},
            "cells": CELL_COUNT,
            "patched": [{"sheet": sheet, "cell": cell}
                        for sheet, cell, _ in registered_cells(spec)],
        }
        # `map` is the engine's hash-bound input part, so it names the shipped
        # spec file itself; the repository-relative name lives in
        # `corrections.spec` above for readers.
        result["map"] = {"path": str(SPEC_PATH), "sha256": SPEC_SHA256}
        result["output"]["path"] = str(out)
        staged_receipt.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        mc.reconfirm_output(mc.OutputAuthorization(**result["majorAuthorization"]),
                            context)
        os.replace(staged_out, out)
        published = True
        os.replace(staged_receipt, receipt_path)
    except Exception:
        if published and out.exists():
            out.unlink()
        raise
    finally:
        for path in (derived_path, staged_out, staged_receipt):
            if path.exists():
                path.unlink()
    return result


def cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    run = sub.add_parser("apply-fy1", help="정정 사본을 만든다")
    run.add_argument("source", type=Path, help="clear/fill을 마친 과수 16시트 사본")
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--receipt", required=True, type=Path)
    run.add_argument("--project", default=None, type=Path,
                     help="프로젝트 정본 폴더")
    run.add_argument("--major", default=None, help="명시 전공 ID (과수만)")
    args = parser.parse_args(argv)
    out, receipt_path = args.out.resolve(), args.receipt.resolve()
    if len({args.source.resolve(), out, receipt_path}) != 3:
        print("BLOCK: source, out, and receipt must be different paths", file=sys.stderr)
        return 2
    staged_out = out.with_name(f".{out.stem}.tmp-{os.getpid()}{out.suffix}")
    staged_receipt = receipt_path.with_name(
        f".{receipt_path.stem}.tmp-{os.getpid()}{receipt_path.suffix}")
    try:
        if out.exists() or receipt_path.exists() or staged_out.exists() or staged_receipt.exists():
            raise FileExistsError("refusing to overwrite output, receipt, or staging path")
        import gg_major_contract as mc
        context = (mc.output_context(args.project, args.major)
                   if args.project is not None else None)
        # Policy B pre-check before any directory is created.
        mc.authorize_output(mc.OUTPUT_SCHOOL_WORKBOOK, context)
        out.parent.mkdir(parents=True, exist_ok=True)
        result = apply_fy1(args.source.resolve(), out, receipt_path,
                           context=context)
        print(json.dumps({"status": "patched",
                          "out": result["output"],
                          "patched": len(result["patched"]),
                          "workbook_profile": result["workbook_profile"]},
                         ensure_ascii=False))
        return 0
    except Exception as exc:
        for path in (staged_out, staged_receipt):
            if path.exists():
                path.unlink()
        import gg_major_contract as mc
        if isinstance(exc, mc.OutputHeldError):
            print(json.dumps({"status": "held", "reason": exc.reason,
                              "detail": str(exc),
                              "guidance": exc.detail.get("guidance")},
                             ensure_ascii=False))
            return 2
        from gg_excel_template import TemplateUnreadable, read_failure_report
        if isinstance(exc, TemplateUnreadable):
            print(json.dumps(read_failure_report(exc, produced=[]),
                             ensure_ascii=False))
            return 2
        if isinstance(exc, (OSError, ValueError, RuntimeError)):
            print(f"BLOCK: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(cli(sys.argv[1:]))
