"""School outline policy; writes occur only inside the guarded core apply hook.

The ownership receipt precedes file creation. Only an explicit identical request
may reuse its exact bytes after an interrupted, uncommitted apply.
"""
import hashlib
import json
import os
from pathlib import Path

import gg_major_contract as majors

CATALOG = Path(__file__).resolve().parents[1] / "references/school-outline-profiles.json"


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _binding(project):
    return majors.binding_from_project(majors.default_registry(), project)


def _profile(binding):
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    profile = catalog["profiles"][binding.major_id]
    if profile["module_version"] != binding.module_version:
        raise ValueError("outline_module_version_mismatch")
    nodes = profile["sections"]
    if profile["state"] not in {"seeded", "no_school_toc"} or not isinstance(nodes, list):
        raise ValueError("outline_profile_invalid")
    if (profile["state"] == "no_school_toc") != (not nodes):
        raise ValueError("outline_profile_invalid")
    if len({n["node_id"] for n in nodes}) != len(nodes) or len({n["path"] for n in nodes}) != len(nodes):
        raise ValueError("outline_profile_duplicate")
    if [n["order"] for n in nodes] != list(range(1, len(nodes) + 1)):
        raise ValueError("outline_profile_order")
    base = CATALOG.parent.parent
    for source in profile.get("sources", []):
        if hashlib.sha256((base / source["path"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("outline_source_hash_mismatch")
    return profile


def outline_state(root: str, project: dict) -> dict:
    """Read only; legacy bound projects without sections need explicit reconfirmation."""
    result = dict(state="not_bound", major_id=None, profile_id=None,
                  sections=len(project.get("sections", {})), reason=None)
    try:
        binding = _binding(project)
        result["major_id"] = binding.major_id
        profile = _profile(binding)
        result["profile_id"] = profile["profile_id"]
        sections = project.get("sections", {})
        owned = [s["outline"] for s in sections.values() if isinstance(s.get("outline"), dict)]
        if any(o.get("major_id") != binding.major_id for o in owned):
            result.update(state="outline_conflict", reason="outline_major_mismatch")
        elif owned and any(o.get("profile_hash") != _hash(profile) for o in owned):
            result.update(state="outline_conflict", reason="outline_profile_mismatch")
        elif sections:
            expected = {s["node_id"] for s in profile["sections"]}
            if len(owned) == len(sections) and set(sections) == expected:
                verify_committed(root, project)
                result["state"] = "seeded"
            else:
                result["state"] = "existing_manuscript"
        elif profile["state"] == "no_school_toc":
            result["state"] = "no_school_toc"
        else:
            result["reason"] = "explicit_reconfirmation_required"
    except (majors.MajorContractError, OSError, ValueError, KeyError, TypeError) as error:
        result["reason"] = str(error)
    return result


def verify_committed(root, project, request_id=None):
    """Verify all seeded paths on an idempotent replay, without consulting a new catalog."""
    import gg_core as core
    for section in project.get("sections", {}).values():
        origin = section.get("outline")
        if not isinstance(origin, dict) or (request_id is not None and origin.get("request_id") != request_id):
            continue
        path = core.local(root, section["path"])
        if core.digest(core.draft(path.read_text(encoding="utf-8"))) != section["draft_hash"]:
            raise ValueError("outline_section_hash_mismatch")


def _receipt_rel(request_id):
    return "migration/outline-" + _hash(request_id) + ".json"


def _receipt(root, request_id):
    """The ownership receipt written before a seed's files were created."""
    if not isinstance(request_id, str) or not request_id:
        return None
    try:
        data = json.loads(Path(root, _receipt_rel(request_id)).read_bytes())
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _seed_text(title):
    import gg_core as core
    return "# " + title + "\n\n" + core.START + "\n" + core.END + "\n"


def untouched_seed_sections(project, root=None):
    """Section ids that are provably still untouched school-outline seeds.

    A chapter the outline policy inserted is a preview of the school's
    structure, not student writing.  Four facts must hold: an ``outline``
    origin, ``status == "empty"``, an empty draft in the record, and — when
    ``root`` is given — the file's current bytes still matching the
    ownership receipt written before the seed was created.  Any mismatch
    makes the section student work again, so nothing is ever dropped
    silently.  The receipt check needs the project root; without it the
    record alone decides (progress display, claims coverage).
    """
    import gg_core as core
    empty = core.digest("")
    found, receipts = set(), {}
    for section_id, section in (project.get("sections") or {}).items():
        origin = section.get("outline")
        if not isinstance(origin, dict):
            continue
        if section.get("status") != "empty" or section.get("claims") != []:
            continue
        if section.get("draft_hash") != empty:
            continue
        path = section.get("path")
        if not isinstance(path, str) or not path:
            continue
        if root is None:
            found.add(section_id)
            continue
        request = origin.get("request_id")
        if request not in receipts:
            receipts[request] = _receipt(root, request)
        files = (receipts[request] or {}).get("files")
        if not isinstance(files, dict) or path not in files:
            continue
        try:
            text = core.local(root, path).read_text(encoding="utf-8")
        except OSError:
            continue
        if core.draft(text):
            continue
        if hashlib.sha256(text.encode()).hexdigest() != files[path]:
            continue
        found.add(section_id)
    return found


def cleanup(root, old, project, request_id):
    """Post-commit file housekeeping for the seeds a rebind walked away from.

    Old seed records the commit removed stop owning their files: a file no
    new record names is deleted, but only while its bytes still match the
    receipt that proved it untouched.  A path the new major's seed reused
    gets its heading rewritten to the new chapter title.  Problems are
    returned, never swallowed — the canonical commit is already final, so
    the caller reports ``committed_cleanup_pending``.
    """
    import gg_core as core
    problems = []
    current = {s.get("path") for s in project["sections"].values()}
    for key, section in old["sections"].items():
        origin = section.get("outline")
        if not isinstance(origin, dict) or key in project["sections"]:
            continue
        rel = section.get("path")
        if not isinstance(rel, str) or not rel or rel in current:
            continue
        try:
            path = core.local(root, rel)
            expected = ((_receipt(root, origin.get("request_id")) or {})
                        .get("files") or {}).get(rel)
            if not expected:
                problems.append({"path": rel, "error": "ownership_receipt_missing"})
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                problems.append({"path": rel, "error": "seed_file_changed"})
                continue
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            problems.append({"path": rel, "error": str(error)})
    for section in project["sections"].values():
        origin = section.get("outline")
        if not isinstance(origin, dict) or origin.get("request_id") != request_id:
            continue
        rel = section.get("path")
        try:
            text = core.local(root, rel).read_text(encoding="utf-8")
            if core.draft(text):
                continue
            if text.splitlines() and text.splitlines()[0].lstrip("# ").strip() == section["title"]:
                continue
            core.atomic(core.local(root, rel),
                        _seed_text(section["title"]).encode())
        except (OSError, KeyError) as error:
            problems.append({"path": rel, "error": str(error)})
    return problems


def prepare(root, old, project, change):
    """Validate the final candidate and seed it; caller holds the real OS guard."""
    import gg_core as core
    for key, section in old["sections"].items():
        if "outline" in section and key in project["sections"]:
            project["sections"][key]["outline"] = dict(section["outline"])
    # No unrelated apply silently repairs legacy projects.
    touched = any(op.get("collection") == "facts" and
                  op.get("value", {}).get("field_id") == majors.COMMON_MAJOR_FIELD
                  for op in change.get("ops", []))
    if not touched:
        return []
    try:
        binding = _binding(project)
    except majors.MajorContractError:
        return []
    sections = project["sections"]
    seeded = {key: section for key, section in old["sections"].items()
              if isinstance(section.get("outline"), dict)}
    # A wrong major must be fixable — but only while nothing was written:
    # every old seed chapter has to pass the four-fact untouched test.
    stale = {key for key, section in seeded.items()
             if section["outline"].get("major_id") != binding.major_id}
    if stale:
        untouched = untouched_seed_sections(old, root)
        if not set(seeded) <= untouched or len(stale) != len(seeded):
            raise core._op("apply", "canonical", "not_committed", "outline_conflict")
    try:
        profile = _profile(binding)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise core._op("apply", "canonical", "not_committed", "outline_policy_invalid",
                       detail=str(error), request_id=change["request_id"]) from error
    phash = _hash(profile)
    for key, section in seeded.items():
        if key in stale:
            continue
        if section["outline"].get("profile_hash") != phash:
            raise ValueError("outline_profile_mismatch")
    if old["sections"] and not stale:
        return []
    retired = {seeded[key]["path"]: seeded[key] for key in stale}
    for key in stale:
        sections.pop(key, None)
    if stale and sections:
        # 옛 빈 seed만 걷고, 학생이 쓴 다른 절은 그대로 둔다(새 seed는 심지 않음).
        try:
            core.validate(project)
        except (KeyError, OSError, ValueError) as error:
            raise core._op("apply", "canonical", "not_committed",
                           "outline_prepare_failed", detail=str(error),
                           request_id=change["request_id"]) from error
        return []
    nodes = profile["sections"]
    if not nodes:
        return []
    expected = {n["node_id"] for n in nodes}
    if sections and set(sections) != expected:
        raise ValueError("outline_caller_sections_incomplete")
    for node in nodes:
        section = sections.get(node["node_id"])
        if section and (any(section.get(k) != node[k] for k in ("title", "order", "path"))
                        or section.get("status") != "empty" or section.get("claims") != []
                        or section.get("draft_hash") != core.digest("")):
            raise ValueError("outline_caller_section_conflict")
    request = change["request_id"]
    files = {n["path"]: _seed_text(n["title"]) for n in nodes}
    receipt_rel = _receipt_rel(request)
    receipt_path = core.local(root, receipt_rel)
    receipt = dict(request_id=request, change_hash=core.digest(change), profile_hash=phash,
                   files={p: hashlib.sha256(text.encode()).hexdigest() for p, text in files.items()})
    existing_receipt = receipt_path.exists()
    if existing_receipt and json.loads(receipt_path.read_text(encoding="utf-8")) != receipt:
        raise ValueError("outline_ownership_conflict")
    for rel, text in files.items():
        relative = Path(rel)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("outline_path_conflict")
        if any((Path(root) / Path(*relative.parts[:i])).is_symlink()
               for i in range(1, len(relative.parts) + 1)):
            raise ValueError("outline_path_conflict")
        path = core.local(root, rel)
        if path.exists():
            if rel in {s["path"] for s in sections.values()}:
                continue
            # 손 안 댄 옛 seed가 쓰던 경로는 새 전공이 같은 장으로 다시
            # 쓸 수 있다. 덮어쓰기는 커밋 뒤 cleanup이 맡는다(재시도 원자성).
            if rel in retired:
                continue
            if not existing_receipt or path.read_bytes() != text.encode():
                raise ValueError("outline_path_conflict: " + rel)
    preserved = [receipt_rel]
    try:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if not existing_receipt:
            with receipt_path.open("x", encoding="utf-8") as stream:
                json.dump(receipt, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        for node in nodes:
            rel = node["path"]
            path = core.local(root, rel)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as stream:
                    preserved.append(rel)
                    stream.write(files[rel])
                    stream.flush()
                    os.fsync(stream.fileno())
            if rel not in preserved:
                preserved.append(rel)
            origin = dict(profile_id=profile["profile_id"], profile_version=profile["profile_version"],
                          profile_hash=phash, major_id=binding.major_id,
                          node_id=node["node_id"], request_id=request)
            if node["node_id"] not in sections:
                sections[node["node_id"]] = dict(id=node["node_id"], title=node["title"],
                    path=rel, order=node["order"], status="empty", claims=[], revision=1,
                    draft_hash=core.digest(core.draft(path.read_text(encoding="utf-8"))))
            sections[node["node_id"]]["outline"] = origin
        core.validate(project)
    except (OSError, ValueError) as error:
        raise core._op("apply", "canonical", "not_committed", "outline_prepare_failed",
                       detail=str(error), request_id=request, preserved_paths=preserved) from error
    return preserved
