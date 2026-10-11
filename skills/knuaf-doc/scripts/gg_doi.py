#!/usr/bin/env python3
"""DOI 실재 조회 — 정본 출처의 DOI를 Crossref로 대조한다 (초안, N3).

gg.py는 "never calls model providers or web services"라 웹 호출은
이 독립 스크립트가 담당한다. 이 스크립트는 읽기 전용이다:
project.json을 직접 쓰지 않으며, 조회 결과 JSON을 출력하면
모델이 스냅샷·발췌 경로와 `gg.py apply`로 정본에 기록한다.

자동으로는 논문의 **실재**만 확인한다(현행 어휘의 source_located).
그 논문이 주장을 뒷받침하는지는 사람이 확인한 뒤 정본 사실의
verification을 claim_supported로 올리고 출처 레코드의 claim_review를
연결한다 — 기계 결과를 사람 확인으로 승격하지 않는다.
네트워크 실패·타임아웃·비DOI는 절대 "확인됨"으로 올리지 않는다.

사용법:
    python3 gg_doi.py <작업폴더>                        정본 출처의 DOI 전부 조회
    python3 gg_doi.py <작업폴더> --source-id R-021      특정 출처만
    python3 gg_doi.py --doi 10.xxxx/yyyy [--doi ...]    임의 DOI 목록
    python3 gg_doi.py <작업폴더> --out sources/web/doi-check.json

옛 verify_doi.py와 다른 점: 02_receipts.jsonl 읽기·쓰기가 없고,
doi-resolved/doi-confirmed 문자열을 쓰지 않는다. 현행 정본
(project.json.sources/facts)만 읽는다.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
API = "https://api.crossref.org/works/"
UA = "knuaf-doc-doi-check/0.1"  # 익명(polite pool 미등록) 호출, 1건씩 순차
DEFAULT_INTERVAL = 1.0
DEFAULT_TIMEOUT = 15
SCHEMA = "gg-doi-check/1"


def extract_doi(text):
    """문자열에서 DOI 하나를 뽑는다. 없으면 None."""
    if not isinstance(text, str):
        return None
    m = DOI_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip(".,;)]}\"'")


def collect_from_project(root, source_ids=None):
    """project.json의 sources 레코드와 facts의 source_refs에서
    DOI 후보를 모은다. 반환: {doi: {"ref_ids": [...], "note": ...}}"""
    project_path = Path(root) / "project.json"
    try:
        project = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("project.json을 읽거나 해석할 수 없음") from None
    wanted = set(source_ids or [])
    found = {}

    def add(doi, ref_id):
        entry = found.setdefault(doi, {"ref_ids": []})
        if ref_id not in entry["ref_ids"]:
            entry["ref_ids"].append(ref_id)

    for sid, s in (project.get("sources") or {}).items():
        if wanted and sid not in wanted:
            continue
        for field in ("doi", "locator", "url"):
            doi = extract_doi(s.get(field))
            if doi:
                add(doi, sid)
    for fid, f in (project.get("facts") or {}).items():
        for ref in f.get("source_refs") or []:
            sid = ref.get("id")
            if wanted and sid not in wanted:
                continue
            doi = extract_doi(ref.get("locator"))
            if doi:
                add(doi, sid or fid)
    if wanted:
        for entry in found.values():
            entry["ref_ids"] = [r for r in entry["ref_ids"] if r in wanted] or entry["ref_ids"]
    return found


def _urllib_fetch(doi, timeout):
    """실제 HTTP 계층. 반환 (status_code|None, body_bytes|None, error|None).
    시험에서는 이 함수를 가짜 응답으로 주입한다."""
    url = API + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read(), None
    except urllib.error.HTTPError as e:
        return e.code, None, "HTTP %s" % e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, None, type(e).__name__


def _parse_message(body, requested_doi):
    """요청 DOI와 일치하는 Crossref 레코드에서 서지 최소 필드를 뽑는다."""
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
        raise ValueError("Crossref 응답에 message 객체가 없음")
    m = data["message"]
    response_doi = m.get("DOI")
    if (not isinstance(response_doi, str) or
            response_doi.strip().casefold() != requested_doi.strip().casefold()):
        raise ValueError("Crossref 응답 DOI가 요청 DOI와 일치하지 않음")
    titles = m.get("title") or []
    if not isinstance(titles, list):
        titles = []
    year = ""
    for key in ("published-print", "published-online", "issued"):
        field = m.get(key)
        parts = field.get("date-parts") if isinstance(field, dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list) \
                and parts[0]:
            year = str(parts[0][0])
            break
    containers = m.get("container-title") or []
    if not isinstance(containers, list):
        containers = []
    return {
        "title": titles[0] if titles else "",
        "year": year,
        "journal": containers[0] if containers else "",
    }


def check_items(items, *, fetch=None, interval=DEFAULT_INTERVAL,
                timeout=DEFAULT_TIMEOUT):
    """items: [{"doi": str|None, "ref_ids": [...]}]
    fetch가 None이면 실제 네트워크를 쓴다. 항목 사이에 interval초 간격."""
    fetch = fetch or _urllib_fetch
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    results = []
    calls = 0
    for item in items:
        if calls:
            time.sleep(interval)
        doi = item.get("doi")
        if not doi:
            results.append({
                "doi": None,
                "ref_ids": item.get("ref_ids") or [],
                "status": "unparseable",
                "checked_at": now,
                "detail": "DOI 형식(10.NNNN/...)을 찾지 못함 — 조회 대상 아님",
            })
            continue
        calls += 1
        status_code, body, error = fetch(doi, timeout)
        base = {"doi": doi, "ref_ids": item.get("ref_ids") or [],
                "checked_at": now}
        if status_code == 200 and body is not None:
            try:
                bib = _parse_message(body, doi)
            except (ValueError, UnicodeError) as e:
                results.append(dict(
                    base, status="network_error", http_status=status_code,
                    detail="응답 해석 실패: %s" % type(e).__name__))
            else:
                results.append(dict(base, status="resolved",
                                    http_status=status_code, **bib))
        elif status_code == 404:
            results.append(dict(base, status="not_found",
                                http_status=status_code,
                                detail="Crossref에 해당 DOI 없음"))
        else:
            results.append(dict(
                base, status="network_error", http_status=status_code,
                detail=error or ("HTTP %s" % status_code)))
    return results


def build_report(root, results):
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "schema": SCHEMA,
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"),
        "results": results,
        "summary": counts,
        "notice": "resolved는 실재 확인(source_located)까지다. 주장 적합성은 "
                  "사람이 원문을 읽고 claim_supported로 별도 승격한다. "
                  "not_found·network_error·unparseable은 확인됨이 아니다.",
    }


def _inside(root, name):
    root = Path(root).resolve()
    p = (root / name).resolve()
    if root not in p.parents and p != root:
        raise ValueError("작업 폴더 밖 경로는 허용하지 않음: " + str(name))
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="DOI 실재 조회 (읽기 전용)")
    ap.add_argument("folder", nargs="?", help="작업폴더 (project.json 위치)")
    ap.add_argument("--source-id", action="append", default=[],
                    help="이 출처 ID만 조회 (반복 가능)")
    ap.add_argument("--doi", action="append", default=[],
                    help="임의 DOI 문자열 (반복 가능)")
    ap.add_argument("--dois", help="DOI 목록 파일 (한 줄에 하나)")
    ap.add_argument("--out", help="결과 JSON을 작업폴더 안 이 경로에 저장")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help="호출 사이 간격(초), 기본 1.0")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    a = ap.parse_args(argv)

    if not (0 <= a.interval <= 60) or not (0 < a.timeout <= 120):
        print(json.dumps({"status": "blocked",
                          "reason": "interval은 0~60초, timeout은 0~120초여야 함"},
                         ensure_ascii=False))
        return 2

    items = []
    root = None
    if a.folder:
        root = Path(a.folder).resolve()
        if not (root / "project.json").is_file():
            print(json.dumps({"status": "blocked",
                              "reason": "project.json 없음"},
                             ensure_ascii=False))
            return 2
        found = collect_from_project(root, a.source_id or None)
        for doi, entry in sorted(found.items()):
            items.append({"doi": doi, "ref_ids": entry["ref_ids"]})
        if a.source_id:
            # 지정한 출처인데 DOI가 없으면 unparseable로 표시해 누락을 드러낸다
            covered = {r for e in found.values() for r in e["ref_ids"]}
            for sid in a.source_id:
                if sid not in covered:
                    items.append({"doi": None, "ref_ids": [sid],
                                  "input": sid})
    for raw in a.doi:
        items.append({"doi": extract_doi(raw), "ref_ids": [], "input": raw})
    if a.dois:
        try:
            lines = Path(a.dois).read_text(encoding="utf-8").splitlines()
        except OSError:
            print(json.dumps({"status": "blocked", "reason": "DOI 목록 파일을 읽을 수 없음"},
                             ensure_ascii=False))
            return 2
        for raw in lines:
            raw = raw.strip()
            if raw:
                items.append({"doi": extract_doi(raw), "ref_ids": [],
                              "input": raw})
    report = build_report(
        root, check_items(items, interval=a.interval, timeout=a.timeout))
    if a.out:
        if not root:
            print(json.dumps({"status": "blocked",
                              "reason": "--out은 작업폴더 지정이 필요"},
                             ensure_ascii=False))
            return 2
        try:
            target = _inside(root, a.out)
        except ValueError as e:
            print(json.dumps({"status": "blocked", "reason": str(e)},
                             ensure_ascii=False))
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x", encoding="utf-8") as f:
                f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        except FileExistsError:
            print(json.dumps({"status": "blocked",
                              "reason": "결과 파일이 이미 있음: 덮어쓰지 않음"},
                             ensure_ascii=False))
            return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
