"""Q1b 참고자료 폴더의 내장 정본 재파싱 스킵 분류기.

메타데이터와 SHA-256만으로 참고자료를 분류한다. 이 모듈은 어떤 문서 파서도
호출하지 않는다 (kordoc·OCR·엑셀 시트 로드 금지). 판정 규칙은 스킬 설계
문서(docs 폴더)를 정본으로 하며, 이 모듈은 그 계약을 코드로 옮긴 것이다.

분류 순서:
  1. 확장자가 문서 목록에 없으면 ignored
  2. Q1a 현재 작성물 경로와 같으면 current_draft (키워드 무관, 항상 우선)
  3. SHA-256이 레지스트리와 일치하면 skip_builtin
  4. 파일명 지문이 레지스트리와 닮았는데(school_guideline 제외) 해시가 다르면
     hash_mismatch
  5. 지침 키워드 후보이고 해시 미등록이면 guideline_unmapped
  6. 그 외는 excerpt_new
"""

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
REGISTRY_PATH = BASE_DIR / "references" / "builtin-sources.json"

_PAREN_DIGIT_RE = re.compile(r"\(\d+\)")
_STRIP_CHARS = (" ", "+", "★")


def _load_registry(path=None):
    target = Path(path) if path is not None else REGISTRY_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_name(value):
    text = unicodedata.normalize("NFC", str(value))
    text = _PAREN_DIGIT_RE.sub("", text)
    for ch in _STRIP_CHARS:
        text = text.replace(ch, "")
    return text


def _find_entry_by_hash(registry, digest):
    for entry in registry.get("entries", []):
        if digest in entry.get("sha256", []):
            return entry
    return None


def _resembles_registry_entry(fingerprint, entries):
    for entry in entries:
        if entry.get("class") == "school_guideline":
            continue
        for needle in entry.get("filename_needles", []):
            if _fingerprint_name(needle) in fingerprint:
                return entry
    return None


def _is_guideline_candidate(fingerprint, guideline_keywords):
    always = guideline_keywords.get("always", [])
    for word in always:
        if _fingerprint_name(word) in fingerprint:
            return True
    form_rule = guideline_keywords.get("form_requires_companion")
    if form_rule:
        keyword = _fingerprint_name(form_rule.get("keyword", ""))
        if keyword and keyword in fingerprint:
            companions = form_rule.get("must_also_match_any", [])
            for companion in companions:
                if _fingerprint_name(companion) in fingerprint:
                    return True
    return False


def classify_reference_file(path, *, current_draft_paths=(), registry=None):
    if registry is None:
        registry = _load_registry()
    path = Path(path)
    resolved = path.resolve()
    name = path.name
    suffix = path.suffix.lower()
    doc_suffixes = {s.lower() for s in registry.get("document_suffixes", [])}

    if suffix not in doc_suffixes:
        return {
            "disposition": "ignored",
            "sha256": None,
            "name": name,
            "parse_omitted": True,
            "status_ko": "문서 확장자 아님 - 분류 대상 제외",
        }

    current_resolved = {Path(p).resolve() for p in current_draft_paths}
    if resolved in current_resolved:
        digest = _sha256_file(path)
        return {
            "disposition": "current_draft",
            "sha256": digest,
            "name": name,
            "parse_omitted": False,
            "status_ko": "현재 작성물 지정 - 참고자료 스킵 대상 아님",
        }

    digest = _sha256_file(path)
    entry = _find_entry_by_hash(registry, digest)
    if entry is not None:
        return {
            "disposition": "skip_builtin",
            "sha256": digest,
            "name": name,
            "mapped_id": entry["id"],
            "parse_omitted": True,
            "status_ko": entry.get("status_ko", "내장 정본 매핑 완료 - 본문 열람 생략"),
        }

    fingerprint = _fingerprint_name(name)
    resembled = _resembles_registry_entry(fingerprint, registry.get("entries", []))
    if resembled is not None:
        return {
            "disposition": "hash_mismatch",
            "sha256": digest,
            "name": name,
            "resembles_id": resembled["id"],
            "parse_omitted": False,
            "status_ko": "파일명이 내장 정본과 유사하나 해시 불일치 - 신규 개정으로 발췌",
        }

    if _is_guideline_candidate(fingerprint, registry.get("guideline_keywords", {})):
        return {
            "disposition": "guideline_unmapped",
            "sha256": digest,
            "name": name,
            "parse_omitted": True,
            "status_ko": "지침 키워드 후보이나 해시 미등록 - 표지·연도만 확인",
        }

    return {
        "disposition": "excerpt_new",
        "sha256": digest,
        "name": name,
        "parse_omitted": False,
        "status_ko": "신규 참고자료 - 발췌 대상",
    }


def catalog_reference_folder(folder, *, current_draft_paths=(), registry=None):
    if registry is None:
        registry = _load_registry()
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError("참고폴더를 찾을 수 없음: " + str(folder))
    current_resolved = tuple(Path(p).resolve() for p in current_draft_paths)
    items = []
    fp_lines = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if fpath.is_symlink():
                continue
            try:
                stat = fpath.stat()
            except OSError:
                continue
            result = classify_reference_file(
                fpath, current_draft_paths=current_resolved, registry=registry
            )
            item = {"name": result["name"], "suffix": fpath.suffix, "size": stat.st_size}
            for key in ("sha256", "disposition", "mapped_id", "resembles_id", "status_ko", "parse_omitted"):
                if key in result:
                    item[key] = result[key]
            items.append(item)
            relpath = fpath.relative_to(root).as_posix()
            fp_lines.append(
                "%s|%d|%d" % (unicodedata.normalize("NFC", relpath), stat.st_size, int(stat.st_mtime))
            )
    folder_fingerprint = hashlib.sha256("\n".join(sorted(fp_lines)).encode("utf-8")).hexdigest()
    return {
        "schema": "gg-source-intake/v1",
        "folder_fingerprint": folder_fingerprint,
        "items": items,
    }


def write_intake_index(work_root, catalog):
    root = Path(work_root)
    root.mkdir(parents=True, exist_ok=True)
    sources_dir = root / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    index_path = sources_dir / "intake-index.json"
    index_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    items = catalog.get("items", [])
    skip_items = [i for i in items if i.get("disposition") == "skip_builtin"]
    unmapped_items = [i for i in items if i.get("disposition") in ("guideline_unmapped", "hash_mismatch")]
    new_items = [i for i in items if i.get("disposition") == "excerpt_new"]

    lines = ["# 참고자료 인덱스", "", "## 1. 내장 정본 (본문 열람 생략)", ""]
    lines += (
        [
            "- %s — %s (mapped_id: %s)" % (i["name"], i.get("status_ko", ""), i.get("mapped_id", ""))
            for i in skip_items
        ]
        or ["- 없음"]
    )
    lines += ["", "## 2. 지침 미매핑·해시 불일치 (정체 확인)", ""]
    lines += (
        [
            "- %s — %s (%s)" % (i["name"], i.get("status_ko", ""), i.get("disposition"))
            for i in unmapped_items
        ]
        or ["- 없음"]
    )
    lines += ["", "## 3. 신규 참고자료 (발췌 대상)", ""]
    lines += (
        ["- %s — %s" % (i["name"], i.get("status_ko", "")) for i in new_items]
        or ["- 없음"]
    )
    lines.append("")

    markdown_path = root / "03_sources.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return {"index_path": str(index_path), "markdown_path": str(markdown_path)}
