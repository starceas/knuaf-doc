#!/usr/bin/env python3
"""PR④ B단계 적용기 — 동결 대응표를 econ 2025·mafra 2024 팩에 그대로 옮긴다.

입력(모두 동결·검토 통과물, 이 스크립트 밖에서 만든다)
  * 대응표 TSV      ``tests/fixtures/source-map-pr4.tsv`` (5,695행)
  * 지역 정정 TSV   ``tests/fixtures/region-fix-pr4.tsv`` (광주·인천 2행)
  * 정정 명세 JSON  ``<mafra 팩>/corrections/*.json`` (E9 1행)
  * 팩 ``records.jsonl``·``manifest.json``

원칙
  * 바뀌는 줄만 손댄다. 나머지 줄은 원래 **바이트 그대로** 출력한다
    (재직렬화 금지 — 줄 지문이 곧 감사 키다).
  * 줄 안에서도 문자열 치환만 한다. ``source`` 는 ``"locator":`` 앞(소득 팩과
    같은 자리), E9 는 명세가 지정한 두 칸만.
  * 입력 검증이 하나라도 실패하면 **아무 파일도 쓰지 않는다**(부분 산출 없음).
  * 멱등: 줄 지문이 before 면 적용, 이미 after 면 통과, 둘 다 아니면 중단.
  * 기본은 검사만(``--check``). 실제 쓰기는 ``--apply``.
  * 입력 검증 후 파일별 atomic replace. 프로세스/디스크 장애까지 여러 파일
    전체의 트랜잭션으로 보장하지는 않는다.

감사 키 = ``sha256(줄 바이트 + "\\n")`` = manifest ``raw_line_sha256``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

MAP_HEADER = [
    "pack_id", "physical_line", "raw_line_sha256_before", "record_id",
    "kind", "locator.section", "locator.pdf_page", "row_label", "class",
    "survey", "publisher", "document", "data_year", "published",
    "via_document", "via_pdf_page", "via_pdf_sha256", "note_page",
    "note_text", "image_checked", "reason",
]
(_C_PACK, _C_LINE, _C_SHA, _C_RID, _C_KIND, _C_SECTION, _C_PDF_PAGE,
 _C_LABEL, _C_CLASS, _C_SURVEY, _C_PUBLISHER, _C_DOC, _C_YEAR, _C_PUBLISHED,
 _C_VIA_DOC, _C_VIA_PAGE, _C_VIA_SHA, _C_NOTE_PAGE, _C_NOTE_TEXT,
 _C_IMAGE, _C_REASON) = range(len(MAP_HEADER))

CLASSES = ("sourced", "unsourced_no_note", "unsourced_insufficient",
           "unsourced_mixed")

ECON_PACK_ID = "rda.econ.2025"
MAFRA_PACK_ID = "mafra.specialty.production.2024"
REGIONAL_PACK_ID = "rda.income.regional.2025"
REGIONAL_PACK_ID_2024 = "rda.income.regional.2024"

PACKS = {
    ECON_PACK_ID: {
        "dir": "skills/knuaf-doc/references/benchmark-packs/common/rda-econ-2025",
        "lines": 2707,
    },
    MAFRA_PACK_ID: {
        "dir": ("skills/knuaf-doc/references/benchmark-packs/majors/"
                "specialty_crops/mafra-specialty-production-2024"),
        "lines": 2988,
    },
    REGIONAL_PACK_ID: {
        "dir": ("skills/knuaf-doc/references/benchmark-packs/common/"
                "rda-income-regional-2025"),
        "lines": 49,
    },
    REGIONAL_PACK_ID_2024: {
        "dir": ("skills/knuaf-doc/references/benchmark-packs/common/"
                "rda-income-regional-2024"),
        "lines": 365,
    },
}

# source 키 순서 — 소득 팩(source 를 가진 유일한 기존 팩)의 상대 순서를 따르고
# ``via`` 만 뒤에 붙인다. 값이 없는 칸은 null 로 남긴다(대응표 빈칸 규칙 N-05).
SOURCE_KEY_ORDER = ("publisher", "survey", "survey_label", "aggregation_group",
                    "data_year", "published", "document", "via")

# source 삽입 기준점: 두 팩 모두 ``"price_character": ..., "locator": ...``.
SOURCE_ANCHOR = '"locator": '
METROPOLITAN_SIDO = ("광주", "인천")



class ApplyError(RuntimeError):
    """검증 실패 — 호출자는 아무것도 쓰지 않은 채로 죽는다."""


# ---------------------------------------------------------------------------
# 입출력 도우미


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def read_tsv(path: Path):
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    if not rows:
        raise ApplyError("빈 TSV: %s" % path)
    header = rows[0]
    body = [row for row in rows[1:] if row]
    return header, body


def read_lines(path: Path):
    """``records.jsonl`` 의 물리 줄을 **바이트 그대로**(개행 포함) 읽는다."""
    blob = path.read_bytes()
    if not blob:
        raise ApplyError("빈 팩 파일: %s" % path)
    lines = blob.splitlines(keepends=True)
    for idx, line in enumerate(lines, start=1):
        if not line.endswith(b"\n") or line.endswith(b"\r\n"):
            raise ApplyError("%s:%d 줄 개행이 LF 가 아니다" % (path, idx))
        if not line.strip():
            raise ApplyError("%s:%d 빈 줄" % (path, idx))
    return lines


def write_atomic(path: Path, blob: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".apply-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# 대응표 → source 사상


def _cell(row, idx):
    value = row[idx]
    return None if value == "null" else value


def source_from_row(row):
    """대응표 1행 → ``source`` dict (설계 E2·E6). class 무관, 순수 사상만."""
    survey = _cell(row, _C_SURVEY)
    data_year = _cell(row, _C_YEAR)
    via_title = _cell(row, _C_VIA_DOC)
    if survey is not None and data_year is None:
        raise ApplyError("%s:%s survey 는 있는데 data_year 가 없다"
                         % (row[_C_PACK], row[_C_LINE]))
    source = {
        "publisher": _cell(row, _C_PUBLISHER),
        "survey": survey,
        "survey_label": None,  # E11: 행 단위 이름 근거가 없어 null만 허용.
        "aggregation_group": (None if survey is None
                              else "%s.%d" % (survey, int(data_year))),
        "data_year": None if data_year is None else int(data_year),
        "published": _cell(row, _C_PUBLISHED),
        "document": _cell(row, _C_DOC),
        "via": (None if via_title is None else {
            "title": via_title,
            "pdf_page": int(_cell(row, _C_VIA_PAGE)),
            "pdf_sha256": _cell(row, _C_VIA_SHA),
        }),
    }
    return {key: source[key] for key in SOURCE_KEY_ORDER}


def _assert_source_contract(record: dict, pack_id: str, line: int) -> None:
    """E3-2 계약을 그 자리에서 확인한다(팩에 사상한 뒤의 자기 검사)."""
    src = record.get("source")
    if not isinstance(src, dict):
        raise ApplyError("%s:%d source 가 dict 가 아니다" % (pack_id, line))
    if tuple(src) != SOURCE_KEY_ORDER:
        raise ApplyError("%s:%d source 키 순서가 다르다: %s"
                         % (pack_id, line, list(src)))
    if src["survey_label"] is not None:
        raise ApplyError("%s:%d survey_label 은 null만 허용(E11)" % (pack_id, line))
    survey = src["survey"]
    year = src["data_year"]
    group = src["aggregation_group"]
    if not isinstance(survey, str) or not survey:
        raise ApplyError("%s:%d survey 없음" % (pack_id, line))
    if isinstance(year, bool) or not isinstance(year, int):
        raise ApplyError("%s:%d data_year 가 int 가 아니다" % (pack_id, line))
    if group != "%s.%d" % (survey, year):
        raise ApplyError("%s:%d aggregation_group 불일치" % (pack_id, line))
    observed = ((record.get("basis") or {}).get("year"))
    if observed != year:
        raise ApplyError("%s:%d data_year(%r) != basis.year(%r) (E2)"
                         % (pack_id, line, year, observed))
    via = src["via"]
    if via is not None:
        if set(via) != {"title", "pdf_page", "pdf_sha256"}:
            raise ApplyError("%s:%d via 키 구성" % (pack_id, line))
        if not isinstance(via["pdf_page"], int):
            raise ApplyError("%s:%d via.pdf_page" % (pack_id, line))


# ---------------------------------------------------------------------------
# 줄 문자열 치환 (재직렬화 금지)


def insert_source(text: str, source: dict) -> str:
    if text.count(SOURCE_ANCHOR) != 1:
        raise ApplyError("source 삽입 기준점 %r 가 정확히 1회가 아니다"
                         % SOURCE_ANCHOR)
    idx = text.index(SOURCE_ANCHOR)
    if not text[:idx].endswith(', '):
        raise ApplyError("기준점 앞이 ', ' 로 끝나지 않는다: %r"
                         % text[max(0, idx - 40):idx])
    payload = '"source": %s, ' % json.dumps(source, ensure_ascii=False)
    return text[:idx] + payload + text[idx:]


def apply_field_changes(text: str, changes):
    """``[{path, before, after}]`` — 줄 안에서 옛 토큰을 새 토큰으로 바꾼다."""
    for change in changes:
        key = change["path"].rsplit(".", 1)[-1]
        old = '"%s": %s' % (key, json.dumps(change["before"],
                                            ensure_ascii=False))
        new = '"%s": %s' % (key, json.dumps(change["after"],
                                            ensure_ascii=False))
        if text.count(old) != 1:
            raise ApplyError("%s 토큰이 정확히 1회가 아니다(%d회)"
                             % (change["path"], text.count(old)))
        text = text.replace(old, new)
    return text


def expected_order(record: dict, with_source: bool = True) -> list:
    """``source`` 를 넣은 뒤의 키 순서(``locator`` 바로 앞, 소득 팩과 같은 자리)."""
    keys = list(record)
    if with_source:
        keys.insert(keys.index("locator"), "source")
    return keys


# ---------------------------------------------------------------------------
# 검증·계획·적용


_TSV_ESCAPES = {"\\t": "\t", "\\n": "\n", "\\r": "\r"}


def _tsv_unescape(cell: str) -> str:
    """대응표 생성기(w4src/map/build_map.py:289)는 셀 안 탭·줄바꿈을
    `\\t`·`\\n`·`\\r` 글자로 적는다(mafra 1928·1939~1941행 맥문동 레코드 id).
    원시 레코드와 비교할 때만 되돌린다."""
    for k, v in _TSV_ESCAPES.items():
        cell = cell.replace(k, v)
    return cell


def load_map(path: Path):
    header, body = read_tsv(path)
    if header != MAP_HEADER:
        raise ApplyError("대응표 헤더가 §1.2 와 다르다: %s" % header)
    if len(body) != 5695:
        raise ApplyError("대응표 행 수 %d != 5695" % len(body))
    table = {}
    for row in body:
        if len(row) != len(MAP_HEADER):
            raise ApplyError("대응표 열 수 %d" % len(row))
        if any(cell == "" for cell in row) or any(
                cell == "null" for cell in row[:_C_CLASS + 1]):
            raise ApplyError("대응표 빈칸 또는 식별열/class null")
        key = (row[_C_PACK], int(row[_C_LINE]))
        if key[0] not in (ECON_PACK_ID, MAFRA_PACK_ID):
            raise ApplyError("대응표 대상 밖 팩: %s" % (key,))
        if key in table:
            raise ApplyError("대응표 중복 행: %s" % (key,))
        if row[_C_CLASS] not in CLASSES:
            raise ApplyError("대응표 class 밖: %s %s" % (key, row[_C_CLASS]))
        if row[_C_CLASS] != "sourced":
            for idx in (_C_SURVEY, _C_PUBLISHER, _C_DOC, _C_YEAR,
                        _C_PUBLISHED, _C_VIA_DOC, _C_VIA_PAGE, _C_VIA_SHA):
                if row[idx] != "null":
                    raise ApplyError("unsourced 행에 출처 값: %s %s"
                                     % (key, MAP_HEADER[idx]))
        table[key] = row
    return table


def load_corrections(paths):
    """E9 한 항목만 허용한다. 메모 속 노드를 정정으로 추측하지 않는다."""
    if len(paths) != 1:
        raise ApplyError("E9 정정 명세는 정확히 1개여야 한다")
    path = paths[0]
    blob = json.loads(path.read_text(encoding="utf-8"))
    if blob.get("schema") != "knuaf-doc.correction-spec/1" \
            or blob.get("issue") != "E9" or len(blob.get("records", [])) != 1:
        raise ApplyError("E9 정정 명세 schema/issue/records 불일치")
    node = blob["records"][0]
    before = {"metrics.farms": None, "metrics.area_ha": "14771117",
              "metrics.harvest_area_ha": "990", "metrics.production_mt": "1500"}
    after = {"metrics.farms": "1477", "metrics.area_ha": "1117"}
    changes = [{"path": key, "before": before[key], "after": value}
               for key, value in after.items()]
    if (node.get("pack_id"), node.get("physical_line")) != (MAFRA_PACK_ID, 195) \
            or node.get("before") != before or node.get("after") != after \
            or node.get("changes") != changes:
        raise ApplyError("E9 대상/두 칸 정정 계약 불일치")
    return [(path, blob, node)]


def pack_pdf_sha256(manifest: dict, pack_id: str) -> str:
    """팩 manifest 행들의 ``source_observation.source_pdf_sha256`` 지문(N-04).

    manifest 최상위에는 이 값이 없다(행마다 있다). 한 팩 안에서 하나여야
    하며, 갈리면 어느 쪽이 그 팩 PDF인지 코드가 정할 수 없으므로 멈춘다.
    """
    seen = set()
    for row in manifest.get("rows") or []:
        observation = row.get("source_observation") or {}
        digest = observation.get("source_pdf_sha256")
        if digest:
            seen.add(digest)
    if len(seen) != 1:
        raise ApplyError("%s: manifest source_pdf_sha256 이 하나가 아니다: %s"
                         % (pack_id, sorted(seen)))
    return seen.pop()


def plan_pack(pack_id: str, root: Path, table, corrections):
    info = PACKS[pack_id]
    pack_dir = root / info["dir"]
    records_path = pack_dir / "records.jsonl"
    lines = read_lines(records_path)
    if len(lines) != info["lines"]:
        raise ApplyError("%s: 줄 수 %d != %d"
                         % (pack_id, len(lines), info["lines"]))
    map_rows = {line: row for (pid, line), row in table.items() if pid == pack_id}
    if sorted(map_rows) != list(range(1, len(lines) + 1)):
        raise ApplyError("%s: 대응표가 1..N을 정확히 덮지 않는다" % pack_id)
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    pdf_digest = pack_pdf_sha256(manifest, pack_id)
    e9 = {node["physical_line"]: (path, blob, node)
          for path, blob, node in corrections if node["pack_id"] == pack_id}
    new_lines, base_lines, correction_items = [], [], []
    changed = sourced = 0
    for idx, current_bytes in enumerate(lines, start=1):
        row = map_rows[idx]
        current_text = current_bytes.decode("utf-8")
        current = json.loads(current_text)
        if current.get("pack_id") != pack_id \
                or current.get("record_id") != _tsv_unescape(row[_C_RID]):
            raise ApplyError("%s:%d pack_id/record_id 불일치" % (pack_id, idx))
        want_source = row[_C_CLASS] == "sourced"
        source = source_from_row(row) if want_source else None
        base_text = current_text
        if "source" in current:
            if not want_source or current["source"] != source:
                raise ApplyError("%s:%d 기존 source가 대응표와 다르다" % (pack_id, idx))
            payload = '"source": %s, ' % json.dumps(source, ensure_ascii=False)
            if base_text.count(payload + SOURCE_ANCHOR) != 1:
                raise ApplyError("%s:%d 기존 source 바이트/위치 불일치" % (pack_id, idx))
            base_text = base_text.replace(payload + SOURCE_ANCHOR, SOURCE_ANCHOR, 1)
        item = e9.get(idx)
        changes = item[2]["changes"] if item else []
        for change in changes:
            section, field = change["path"].split(".")
            value = current[section][field]
            if value == change["after"]:
                base_text = apply_field_changes(base_text, [{
                    "path": change["path"], "before": change["after"],
                    "after": change["before"],
                }])
            elif value != change["before"]:
                raise ApplyError("%s:%d 정정 전후 어느 값도 아니다" % (pack_id, idx))
        base_bytes = base_text.encode("utf-8")
        # 재실행에도 source/정정만 역변환한 원시 줄이 동결 감사 키와 같아야 한다.
        if sha256_bytes(base_bytes) != row[_C_SHA]:
            raise ApplyError("%s:%d 동결 before 감사 키 불일치" % (pack_id, idx))
        base = json.loads(base_text)
        expected = json.loads(base_text)
        if (base.get("kind") != row[_C_KIND]
                or base["locator"]["section"] != row[_C_SECTION]
                or base["locator"]["pdf_page"] != int(row[_C_PDF_PAGE])):
            raise ApplyError("%s:%d 대응표 행 신원 불일치" % (pack_id, idx))
        new_text = base_text
        if want_source:
            via = source["via"]
            if pack_id == MAFRA_PACK_ID and source["survey"] == "mafra.specialty_admin":
                if via is not None:
                    raise ApplyError("%s:%d 생산실적 via 금지" % (pack_id, idx))
            elif via is None or via["pdf_sha256"] != pdf_digest:
                raise ApplyError("%s:%d via/PDF 지문 불일치" % (pack_id, idx))
            expected["source"] = source
            new_text = insert_source(new_text, source)
            sourced += 1
        if item:
            node = item[2]
            if node["record_id"] != base["record_id"]:
                raise ApplyError("E9 record_id 불일치")
            for key, value in node["before"].items():
                section, field = key.split(".")
                if base[section][field] != value:
                    raise ApplyError("E9 before 값 불일치: %s" % key)
            for change in changes:
                section, field = change["path"].split(".")
                expected[section][field] = change["after"]
            new_text = apply_field_changes(new_text, changes)
        new_bytes = new_text.encode("utf-8")
        if current_bytes not in (base_bytes, new_bytes):
            raise ApplyError("%s:%d 완전한 before/after 줄이 아니다" % (pack_id, idx))
        parsed = json.loads(new_text)
        if parsed != expected or list(parsed) != expected_order(base, want_source):
            raise ApplyError("%s:%d 허용 필드 밖 변경/키 순서 변경" % (pack_id, idx))
        if want_source:
            _assert_source_contract(parsed, pack_id, idx)
        changed += current_bytes != new_bytes
        base_lines.append(base_bytes)
        new_lines.append(new_bytes)
        if item:
            correction_items.append({
                "path": str(item[0]), "blob": item[1], "node": item[2],
                "line": idx, "before_digest": row[_C_SHA],
                "after_digest": sha256_bytes(new_bytes),
            })
    new_blob = b"".join(new_lines)
    return new_blob, {
        "pack_id": pack_id, "records_path": str(records_path), "lines": len(lines),
        "changed": changed, "sourced": sourced,
        "corrected": sum(len(item[2]["changes"]) for item in e9.values()),
        "base_file_sha256": sha256_bytes(b"".join(base_lines)),
        "new_file_sha256": sha256_bytes(new_blob),
        "unchanged_bytes": new_blob == b"".join(lines),
        "corrections": correction_items,
    }


def plan_region_fix(root: Path, region_rows, table):
    if len(region_rows) != 2 or any(len(row) != 7 for row in region_rows):
        raise ApplyError("region-fix는 7열 2행이어야 한다")
    if {(row[0], int(row[1])) for row in region_rows} != {
            (REGIONAL_PACK_ID, 40), (REGIONAL_PACK_ID, 41)}:
        raise ApplyError("region-fix 대상은 2025 지역 40·41행뿐이다")
    path = root / PACKS[REGIONAL_PACK_ID]["dir"] / "records.jsonl"
    lines = read_lines(path)
    if len(lines) != PACKS[REGIONAL_PACK_ID]["lines"]:
        raise ApplyError("2025 지역 팩 줄 수 불일치")
    new_lines = list(lines)
    changed = 0
    for row in region_rows:
        pack, line_no, digest_before, record_id, before, after, _reason = row
        idx = int(line_no)
        if (pack, idx) in table or (before, after) != ("provincial", "metropolitan"):
            raise ApplyError("region-fix 대응표 분리/level 계약 불일치")
        text = lines[idx - 1].decode("utf-8")
        record = json.loads(text)
        region = record.get("region") or {}
        if record.get("pack_id") != pack or record.get("record_id") != record_id \
                or region.get("sido") not in METROPOLITAN_SIDO:
            raise ApplyError("%s:%d 지역 행 신원 불일치" % (pack, idx))
        if region.get("level") == after:
            base_text = apply_field_changes(text, [{
                "path": "region.level", "before": after, "after": before}])
        elif region.get("level") == before:
            base_text = text
        else:
            raise ApplyError("%s:%d level 불일치" % (pack, idx))
        if sha256_bytes(base_text.encode("utf-8")) != digest_before:
            raise ApplyError("%s:%d 동결 before 감사 키 불일치" % (pack, idx))
        new_text = apply_field_changes(base_text, [{
            "path": "region.level", "before": before, "after": after}])
        expected = json.loads(base_text)
        expected["region"]["level"] = after
        if json.loads(new_text) != expected:
            raise ApplyError("%s:%d level 밖 변경" % (pack, idx))
        new_lines[idx - 1] = new_text.encode("utf-8")
        changed += new_lines[idx - 1] != lines[idx - 1]
    for pack_id in (REGIONAL_PACK_ID, REGIONAL_PACK_ID_2024):
        candidate = new_lines if pack_id == REGIONAL_PACK_ID else read_lines(
            root / PACKS[pack_id]["dir"] / "records.jsonl")
        if len(candidate) != PACKS[pack_id]["lines"]:
            raise ApplyError("%s 지역 팩 줄 수 불일치" % pack_id)
        for line in candidate:
            region = json.loads(line).get("region") or {}
            if region.get("sido") in METROPOLITAN_SIDO and region.get("level") == "provincial":
                raise ApplyError("광역시 provincial 잔존: %s" % pack_id)
    blob = b"".join(new_lines)
    return blob, {"pack_id": REGIONAL_PACK_ID, "records_path": str(path),
                  "lines": len(lines), "changed": changed,
                  "new_file_sha256": sha256_bytes(blob)}


def fill_correction_digests(corrections_summary, pack_id, file_sha_before,
                            file_sha_after):
    """최종 source+정정 바이트로 after를 계산·검증한다. 여기서는 쓰지 않는다.

    before는 역변환한 모든 줄이 동결 감사 키와 같은지 대조한 파일이다.
    비어 있는 after만 채우며, 이미 고정된 after의 불일치는 거부한다.
    """
    writes = []
    for item in corrections_summary:
        node, blob = item["node"], item["blob"]
        expected_before = {
            "pack_id": pack_id, "physical_jsonl_line_1based": item["line"],
            "raw_line_sha256": item["before_digest"],
            "records_file_sha256": file_sha_before,
        }
        expected_after = {
            "pack_id": pack_id, "physical_jsonl_line_1based": item["line"],
            "raw_line_sha256": item["after_digest"],
            "records_file_sha256": file_sha_after,
        }
        if node.get("audit_key_before") != expected_before:
            raise ApplyError("E9 before 줄/파일 감사 키 불일치")
        if node.get("audit_key_after") not in ({}, expected_after):
            raise ApplyError("E9 after 줄/파일 감사 키 불일치")
        node["audit_key_after"] = expected_after
        writes.append((Path(item["path"]),
                       (json.dumps(blob, ensure_ascii=False, indent=2) + "\n").encode("utf-8")))
    return writes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None,
                        help="저장소 루트(기본: 이 파일의 부모의 부모)")
    parser.add_argument("--map", dest="map_path", default=None,
                        help="대응표 TSV(기본: 동결 fixture)")
    parser.add_argument("--region-fix", dest="region_path", default=None,
                        help="광주·인천 TSV(기본: 동결 fixture)")
    parser.add_argument("--corrections", default=None,
                        help="정정 명세 폴더(기본: mafra 팩 corrections/)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="팩/명세를 실제로 쓴다")
    mode.add_argument("--check", action="store_true", help="검사만 한다(기본)")
    parser.add_argument("--json", action="store_true",
                        help="요약을 JSON 한 덩어리로 출력")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root \
        else Path(__file__).resolve().parents[1]
    fixtures = root / "skills" / "knuaf-doc" / "tests" / "fixtures"
    map_path = Path(args.map_path) if args.map_path \
        else fixtures / "source-map-pr4.tsv"
    region_path = Path(args.region_path) if args.region_path \
        else fixtures / "region-fix-pr4.tsv"
    corrections_dir = Path(args.corrections) if args.corrections \
        else root / PACKS[MAFRA_PACK_ID]["dir"] / "corrections"

    try:
        table = load_map(map_path)
        region_header, region_rows = read_tsv(region_path)
        if region_header != ["pack_id", "physical_line", "raw_line_sha256_before",
                             "record_id", "before_level", "after_level", "reason"]:
            raise ApplyError("region-fix 헤더 불일치")
        correction_paths = sorted(p for p in corrections_dir.rglob("*.json")
                                  if p.is_file())
        if not correction_paths:
            raise ApplyError("정정 명세 JSON 이 없다: %s" % corrections_dir)
        corrections = load_corrections(correction_paths)

        writes = []
        summaries = []
        for pack_id in (ECON_PACK_ID, MAFRA_PACK_ID):
            blob, summary = plan_pack(pack_id, root, table, corrections)
            writes.append((Path(summary["records_path"]), blob))
            summaries.append(summary)

        region_blob, region_summary = plan_region_fix(root, region_rows, table)
        writes.append((Path(region_summary["records_path"]), region_blob))
        summaries.append(region_summary)

        for summary in summaries:
            if not summary.get("corrections"):
                continue
            writes.extend(fill_correction_digests(
                summary["corrections"], summary["pack_id"],
                summary["base_file_sha256"], summary["new_file_sha256"]))

        # 명세 검증까지 끝내고 쓴다. 검증 실패 시 부분 산출 없음.
        if args.apply:
            for path, blob in writes:
                if path.read_bytes() != blob:
                    write_atomic(path, blob)

        report = {
            "applied": bool(args.apply),
            "map": str(map_path),
            "map_rows": len(table),
            "packs": [
                {key: value for key, value in summary.items()
                 if key != "corrections"}
                for summary in summaries
            ],
            "corrections": [
                {"pack_id": summary["pack_id"],
                 "line": item["line"],
                 "path": item["path"],
                 "before_sha256": item["before_digest"],
                 "after_sha256": item["after_digest"]}
                for summary in summaries
                for item in summary.get("corrections") or []
            ],
        }
    except (ApplyError, OSError, ValueError, KeyError, TypeError) as exc:
        print("STOP: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("모드: %s" % ("적용" if report["applied"] else "검사만(--apply 없음)"))
        print("대응표 %d행  %s" % (report["map_rows"], report["map"]))
        for summary in report["packs"]:
            print("  %-32s 줄 %s 변경 %s (sourced %s, 정정 %s칸)"
                  "  file %s -> %s"
                  % (summary["pack_id"], summary["lines"], summary["changed"],
                     summary.get("sourced"), summary.get("corrected"),
                     (summary.get("base_file_sha256") or "-")[:12],
                     summary["new_file_sha256"][:12]))
        for item in report["corrections"]:
            print("  정정 %s:%s after=%s"
                  % (item["pack_id"], item["line"],
                     item["after_sha256"][:16]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
