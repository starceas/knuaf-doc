"""Validated skill-owned contracts for relay jobs and lane requests."""

import ctypes
import errno
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import re
import stat
import sys
import uuid
from urllib.parse import urlsplit

import gg_core
import gg_fs

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "delegation.json"
GUI_DIR = ".knuaf-gui"
JOBS_DIR = "jobs"
LANES_REQUEST = "lanes-request.json"
LANES = frozenset({"design", "research", "writing", "repeat", "review"})
JOB_LANES = frozenset({"research", "writing", "repeat", "review"})
PROVIDERS = frozenset({"claude", "codex"})
JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]+\Z", re.ASCII)
QUESTION_ID_RE = re.compile(r"Q[1-9][0-9]*\Z", re.ASCII)
HASH_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
QUANTITATIVE_TYPES = (
    "도매", "소매", "가격", "농가 수취가", "생산량", "재배면적", "통계",
    "수치", "물가", "노무비", "인건비", "소득", "경영비",
)
SOURCE_STATES = frozenset({"증거 확보", "조건 불일치"})
UNCONFIRMED_STATES = frozenset({"조건 불일치", "자료 없음", "접근 실패"})
ROUTE_STATES = frozenset({"성공", "실패"})

_SOURCE_COLUMNS = (
    "질문 ID", "주장 (확인 사실)", "질문 최종 상태", "확인한 원출처", "기준연도",
    "페이지·표·URL", "조회 조건 (지역·기간·필터)", "행·열 위치", "조회 시각",
    "스냅샷 파일 및 해시 (또는 식별 전용 연결)", "열람 상태", "부가 정보", "발견 경로",
)
_UNCONFIRMED_COLUMNS = (
    "질문 ID", "목표 지표", "질문 최종 상태", "미확인 사유 및 후보 내역",
)
_ROUTE_COLUMNS = (
    "질문 ID", "경로 번호", "열어본 경로 (기관·자료명·URL)",
    "경로 탐색 결과 (성공 / 실패)", "상세 사유",
)
_EXCLUDED_COLUMNS = (
    "질문 ID", "배제한 출처 (기관/매체명, URL)", "유형", "배제 사유",
)
_UNIT_COLUMNS = (
    "질문 ID", "원래 단위", "포장 중량·규격", "유통 단계", "거래 조건", "원래 관측값",
)


class DelegationError(ValueError):
    """A stable, non-sensitive rejection reason for a delegation request."""


def _reject(reason):
    raise DelegationError(reason)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _object(value, reason):
    if not isinstance(value, dict):
        _reject(reason)
    return value


def _keys(value, required, optional=(), reason="request_invalid"):
    keys = set(value)
    if not keys.issubset(set(required) | set(optional)) or not set(required).issubset(keys):
        _reject(reason)


def _load_contract(path=None):
    path = Path(path) if path is not None else CONTRACT_PATH
    try:
        if stat.S_ISLNK(path.lstat().st_mode) or not stat.S_ISREG(path.stat().st_mode):
            _reject("delegation_contract_invalid")
        contract = json.loads(path.read_text(encoding="utf-8"))
    except DelegationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _reject("delegation_contract_unavailable")
    _object(contract, "delegation_contract_invalid")
    if set(contract) != {"jobSchema", "laneRequestSchema", "contractVersion"}:
        _reject("delegation_contract_invalid")
    for key in ("jobSchema", "laneRequestSchema"):
        if type(contract[key]) is not int or contract[key] < 1:
            _reject("delegation_contract_invalid")
    if not _nonempty(contract["contractVersion"]):
        _reject("delegation_contract_invalid")
    return contract


def _root(path):
    try:
        root = Path(path).resolve(strict=True)
        if not root.is_dir():
            _reject("workspace_unavailable")
        return root
    except DelegationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        _reject("workspace_unavailable")



def _ensure_output_directory(root, parts):
    with _pinned_directory(root, parts, create=True):
        pass
    return Path(root).joinpath(*parts)



def _gui(root, *, create):
    if create:
        with _pinned_directory(root, (GUI_DIR,), create=True):
            pass
    else:
        with _pinned_directory(root, (GUI_DIR,), create=False):
            pass
    return Path(root) / GUI_DIR


def _jobs(root, *, create):
    if create:
        with _pinned_directory(root, (GUI_DIR, JOBS_DIR), create=True):
            pass
    else:
        with _pinned_directory(root, (GUI_DIR, JOBS_DIR), create=False):
            pass
    return Path(root) / GUI_DIR / JOBS_DIR
class _PinnedDirectory:
    """Pin the output directory chain for the complete publication."""

    def __init__(self, root, parts, *, create):
        self.root = Path(root)
        self.path = self.root.joinpath(*parts)
        self.fds = []
        self.edges = []
        self.windows = None
        if sys.platform == "win32":
            self.windows = gg_fs.gg_winfs.Directory(self.path, create=create)
            self.identity = gg_fs.identity(self.path, kind="directory")
            return
        flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                 | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        try:
            current = os.open(self.root.anchor or "/", flags)
            self.fds.append(current)
            for name in (*self.root.parts[1:], *parts):
                try:
                    child = os.open(name, flags, dir_fd=current)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(name, dir_fd=current)
                    except FileExistsError:
                        pass
                    child = os.open(name, flags, dir_fd=current)
                self.fds.append(child)
                self.edges.append((current, name, child))
                current = child
            self.fd = current
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.windows is not None:
            gg_fs.assert_identity(self.path, self.identity)
            return
        for parent_fd, name, child_fd in self.edges:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened = os.fstat(child_fd)
            if (stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode)
                    or named.st_dev != opened.st_dev
                    or named.st_ino != opened.st_ino):
                _reject("delegation_directory_unsafe")

    def close(self):
        if self.windows is not None:
            self.windows.close()
            self.windows = None
        while self.fds:
            os.close(self.fds.pop())


@contextmanager
def _pinned_directory(root, parts, *, create):
    try:
        directory = _PinnedDirectory(root, parts, create=create)
    except DelegationError:
        raise
    except (gg_fs.FsError, OSError, ValueError, RuntimeError):
        _reject("delegation_directory_unsafe")
    try:
        yield directory
    finally:
        directory.close()


def _entry_stat(directory, name):
    if directory.windows is not None:
        path = directory.path / name
        try:
            info = gg_fs.gg_winfs.lstat(path)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            _reject("delegation_directory_unsafe")
    else:
        try:
            info = os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError:
            _reject("delegation_directory_unsafe")
        if stat.S_ISLNK(info.st_mode):
            _reject("delegation_directory_unsafe")
    return info


def _read_entry(directory, name):
    if directory.windows is not None:
        return gg_fs.read_bytes(directory.path / name)[0]
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=directory.fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _reject("job_registry_unreadable")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _existing_outputs_pinned(directory):
    owned = []
    try:
        names = (gg_fs.gg_winfs.names(directory.path)
                 if directory.windows is not None else os.listdir(directory.fd))
    except OSError:
        _reject("job_registry_unreadable")
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        try:
            raw = _read_entry(directory, name)
            record = json.loads(raw.decode("utf-8"))
        except (gg_fs.FsError, OSError, UnicodeError, json.JSONDecodeError):
            _reject("job_registry_unreadable")
        if not isinstance(record, dict):
            _reject("job_registry_unreadable")
        spec = record.get("spec", record)
        if not isinstance(spec, dict) or not isinstance(spec.get("outputs"), list):
            _reject("job_registry_unreadable")
        outputs = spec["outputs"]
        if not all(_relative_output(item) for item in outputs):
            _reject("job_registry_unreadable")
        owned.extend(outputs)
    return owned


def _rename_noreplace_at(directory_fd, source, destination):
    libc = gg_fs._get_libc()
    if sys.platform == "darwin":
        function, flag = getattr(libc, "renameatx_np", None), gg_fs._RENAME_EXCL
    elif sys.platform.startswith("linux"):
        function, flag = getattr(libc, "renameat2", None), gg_fs._RENAME_NOREPLACE
    else:
        _reject("delegation_atomic_publish_unsupported")
    if function is None:
        _reject("delegation_atomic_publish_unsupported")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p,
                         ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(directory_fd, os.fsencode(source), directory_fd,
                os.fsencode(destination), flag):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), destination)


def _publish_bytes(directory, name, data, *, replace):
    temporary = ".%s.%s.tmp" % (name, uuid.uuid4().hex)
    temporary_identity = None
    descriptor = None
    published = False
    failure = None
    cleanup_failed = False
    try:
        directory.check()
        existing = _entry_stat(directory, name)
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode):
                _reject("lane_request_target_unsafe" if replace
                        else "job_id_exists")
            if not replace:
                _reject("job_id_exists")
        original = (gg_fs._identity_from_stat(existing, "file", directory.path / name)
                    if existing is not None else None)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if directory.windows is not None:
            descriptor = gg_fs._open_nofollow(directory.path / temporary, flags)
        else:
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(temporary, flags, 0o600, dir_fd=directory.fd)
        temporary_identity = gg_fs.fd_identity(descriptor, kind="file")
        directory.check()
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None

        directory.check()
        current = _entry_stat(directory, name)
        current_identity = (
            gg_fs._identity_from_stat(current, "file", directory.path / name)
            if current is not None else None)
        if ((original is None) != (current_identity is None)
                or (original is not None
                    and not gg_fs.same_identity(original, current_identity))):
            _reject("lane_request_target_changed" if replace
                    else "job_id_exists")
        temp_stat = _entry_stat(directory, temporary)
        temp_identity = (gg_fs._identity_from_stat(
            temp_stat, "file", directory.path / temporary)
            if temp_stat is not None else None)
        if not gg_fs.same_identity(temporary_identity, temp_identity):
            _reject("delegation_write_verify_failed")
        directory.check()
        if directory.windows is not None:
            source, target = directory.path / temporary, directory.path / name
            if replace and original is not None:
                os.replace(str(source), str(target))
            elif replace:
                os.rename(str(source), str(target))
            else:
                os.rename(str(source), str(target))
        elif replace and original is not None:
            os.replace(temporary, name, src_dir_fd=directory.fd,
                       dst_dir_fd=directory.fd)
        elif replace:
            _rename_noreplace_at(directory.fd, temporary, name)
        else:
            _rename_noreplace_at(directory.fd, temporary, name)
        published = True
        directory.check()
        saved_stat = _entry_stat(directory, name)
        saved_identity = (gg_fs._identity_from_stat(
            saved_stat, "file", directory.path / name)
            if saved_stat is not None else None)
        if not gg_fs.same_identity(temporary_identity, saved_identity):
            _reject("delegation_write_verify_failed")
        saved = _read_entry(directory, name)
        if hashlib.sha256(saved).digest() != hashlib.sha256(data).digest():
            _reject("delegation_write_verify_failed")
    except DelegationError as error:
        failure = error
    except (gg_fs.FsError, OSError, ValueError, RuntimeError) as error:
        failure = error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if temporary_identity is not None and not published:
            try:
                if directory.windows is not None:
                    directory.check()
                temp_stat = _entry_stat(directory, temporary)
                temp_identity = (gg_fs._identity_from_stat(
                    temp_stat, "file", directory.path / temporary)
                    if temp_stat is not None else None)
                if gg_fs.same_identity(temporary_identity, temp_identity):
                    if directory.windows is not None:
                        gg_fs.unlink_file(directory.path / temporary,
                                          expected_identity=temporary_identity)
                    else:
                        os.unlink(temporary, dir_fd=directory.fd)
            except (DelegationError, gg_fs.FsError, OSError, ValueError):
                cleanup_failed = True
    if failure is not None:
        if cleanup_failed and temporary_identity is not None and not published:
            _reject("delegation_write_cleanup_pending:" +
                    Path(*directory.path.relative_to(directory.root).parts,
                         temporary).as_posix())
        if isinstance(failure, DelegationError):
            raise failure
        _reject("delegation_write_failed")


def _relative_output(value):
    if not isinstance(value, str) or value != value.strip() or "\\" in value:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        gg_fs.validate_relative_path(value)
    except ValueError:
        return False
    return True


def _input(root, value):
    item = _object(value, "input_reference_invalid")
    _keys(item, {"sourceId", "path", "hash", "section"},
          reason="input_reference_invalid")
    if not all(_nonempty(item[key]) for key in ("sourceId", "path", "section")):
        _reject("input_reference_invalid")
    if not isinstance(item["hash"], str) or not HASH_RE.fullmatch(item["hash"]):
        _reject("input_hash_invalid")
    try:
        if not Path(item["path"]).is_absolute():
            _reject("input_reference_invalid")
        path = gg_core.local(root, item["path"])
        data, _identity = gg_fs.read_bytes(path)
    except DelegationError:
        raise
    except (gg_fs.FsError, OSError, RuntimeError, TypeError, ValueError):
        _reject("input_reference_unavailable")
    actual = hashlib.sha256(data).hexdigest()
    if actual != item["hash"]:
        _reject("input_hash_mismatch")
    return {"sourceId": item["sourceId"], "path": str(path),
            "hash": actual, "section": item["section"]}


def _input_from_path(root, source_id, name, section):
    if not all(_nonempty(value) for value in (source_id, name, section)):
        _reject("input_reference_invalid")
    try:
        gg_fs.validate_relative_path(name)
        if "\\" in name:
            _reject("input_reference_invalid")
        path = gg_core.local(root, name)
        data, _identity = gg_fs.read_bytes(path)
    except DelegationError:
        raise
    except (gg_fs.FsError, OSError, RuntimeError, TypeError, ValueError):
        _reject("input_reference_unavailable")
    return {"sourceId": source_id, "path": str(path),
            "hash": hashlib.sha256(data).hexdigest(), "section": section}


def _research(value, outputs):
    research = _object(value, "research_contract_invalid")
    required = {"research_type", "questions", "routes", "keywords", "output_file"}
    if not required.issubset(research):
        _reject("research_contract_invalid")
    if not _nonempty(research["research_type"]):
        _reject("research_type_required")
    if not _relative_output(research["output_file"]):
        _reject("research_output_invalid")
    if research["output_file"] not in outputs:
        _reject("research_output_not_owned")
    questions = research["questions"]
    if not isinstance(questions, list) or not questions:
        _reject("research_questions_required")
    question_ids = set()
    normalized_questions = []
    for question in questions:
        question = _object(question, "research_question_invalid")
        _keys(question, {"id", "target_indicator", "acceptance_conditions"},
              reason="research_question_invalid")
        identifier = question["id"]
        if not isinstance(identifier, str) or not QUESTION_ID_RE.fullmatch(identifier):
            _reject("research_question_id_invalid")
        if identifier in question_ids:
            _reject("research_question_id_duplicate")
        target = question["target_indicator"]
        if not _nonempty(target):
            _reject("research_question_target_required")
        conditions = _object(question["acceptance_conditions"],
                             "research_question_conditions_invalid")
        if not conditions or any(not _nonempty(key) or not _nonempty(item)
                                 for key, item in conditions.items()):
            _reject("research_question_conditions_required")
        question_ids.add(identifier)
        normalized_questions.append({
            "id": identifier,
            "target_indicator": target.strip(),
            "acceptance_conditions": {key.strip(): item.strip()
                                      for key, item in conditions.items()},
        })
    routes = research["routes"]
    if not isinstance(routes, list) or not routes:
        _reject("research_routes_required")
    normalized_routes = []
    route_pairs = set()
    for route in routes:
        route = _object(route, "research_route_invalid")
        _keys(route, {"doc", "id_or_name", "url"}, reason="research_route_invalid")
        if not all(_nonempty(route[key]) for key in ("doc", "id_or_name", "url")):
            _reject("research_route_invalid")
        try:
            raw_url = route["url"]
            url = urlsplit(raw_url)
            _port = url.port
            valid_url = (url.scheme in {"http", "https"} and bool(url.hostname)
                         and url.username is None and url.password is None
                         and not any(char.isspace() or ord(char) < 32 for char in raw_url)
                         and raw_url == raw_url.strip() and not raw_url.endswith("#"))
        except (TypeError, ValueError):
            valid_url = False
        if not valid_url:
            _reject("research_route_url_invalid")
        pair = (route["id_or_name"].strip(), route["url"].strip())
        if pair in route_pairs:
            _reject("research_route_duplicate")
        route_pairs.add(pair)
        normalized_routes.append({"doc": route["doc"].strip(),
                                  "id_or_name": pair[0], "url": pair[1]})
    keywords = research["keywords"]
    if (not isinstance(keywords, list) or not keywords
            or not all(_nonempty(item) for item in keywords)):
        _reject("research_keywords_required")
    return {
        "research_type": research["research_type"].strip(),
        "questions": normalized_questions,
        "routes": normalized_routes,
        "keywords": list(dict.fromkeys(item.strip() for item in keywords)),
        "output_file": research["output_file"],
        **{key: value for key, value in research.items()
           if key not in {"research_type", "questions", "routes", "keywords", "output_file"}},
    }



def _outputs_overlap(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _publish_new(root, path, data):
    try:
        with _pinned_directory(root, (GUI_DIR, JOBS_DIR),
                               create=False) as directory:
            _publish_bytes(directory, path.name, data, replace=False)
    except DelegationError:
        raise
    except (OSError, ValueError, RuntimeError):
        _reject("delegation_write_failed")


def _publish_replace(root, path, data):
    try:
        with _pinned_directory(root, (GUI_DIR,),
                               create=False) as directory:
            _publish_bytes(directory, path.name, data, replace=True)
    except DelegationError:
        raise
    except (OSError, ValueError, RuntimeError):
        _reject("delegation_write_failed")


def _parse_tuple(raw, count, reason):
    if not isinstance(raw, str):
        _reject(reason)
    parts = raw.split("|", count - 1)
    if len(parts) != count or not all(_nonempty(part) for part in parts):
        _reject(reason)
    return [part.strip() for part in parts]


def _parse_question(raw):
    identifier, target, condition_text = _parse_tuple(raw, 3, "research_question_invalid")
    conditions = {}
    for item in condition_text.split(";"):
        if "=" not in item:
            _reject("research_question_conditions_invalid")
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        if not _nonempty(key) or not _nonempty(value) or key in conditions:
            _reject("research_question_conditions_invalid")
        conditions[key] = value
    return {"id": identifier, "target_indicator": target,
            "acceptance_conditions": conditions}


def job_request_from_args(root, *, lane, task, input_refs, outputs, company,
                          model, effort=None, job_id=None, research_type=None,
                          questions=(), routes=(), keywords=(), output_file=None):
    root = _root(root)
    if lane is None:
        _reject("--lane required")
    if not isinstance(lane, str) or lane not in JOB_LANES:
        _reject("job_lane_invalid")
    if not _nonempty(task):
        _reject("job_task_required")
    if not isinstance(company, str) or company not in PROVIDERS:
        _reject("job_company_invalid")
    if not isinstance(model, str) or not _nonempty(model) or "/" in model:
        _reject("job_model_invalid")
    if effort is not None and (not isinstance(effort, str) or not _nonempty(effort)):
        _reject("job_effort_invalid")
    identifier = job_id if job_id is not None else str(uuid.uuid4())
    if not isinstance(identifier, str) or not JOB_ID_RE.fullmatch(identifier):
        _reject("job_id_invalid")
    if not isinstance(input_refs, (list, tuple)):
        _reject("job_inputs_invalid")
    inputs = []
    for raw in input_refs:
        source_id, path, section = _parse_tuple(raw, 3, "input_reference_invalid")
        inputs.append(_input_from_path(root, source_id, path, section))
    if not isinstance(outputs, (list, tuple)) or not outputs:
        _reject("job_outputs_required")
    if not all(_relative_output(item) for item in outputs):
        _reject("job_output_invalid")
    if len(set(outputs)) != len(outputs):
        _reject("job_output_duplicate")
    for index, left in enumerate(outputs):
        if any(_outputs_overlap(left, right) for right in outputs[index + 1:]):
            _reject("job_output_overlap")
    request = {"id": identifier, "lane": lane, "task": task.strip(),
               "inputs": inputs, "outputs": list(outputs),
               "company": company, "model": model.strip(),
               "effort": effort.strip() if effort is not None else None}
    research_values = (research_type, questions, routes, keywords, output_file)
    if lane == "research":
        if not all((_nonempty(research_type), _nonempty(output_file),
                    questions, routes, keywords)):
            _reject("research_contract_required")
        request["research"] = {
            "research_type": research_type.strip(),
            "questions": [_parse_question(item) for item in questions],
            "routes": [dict(zip(("doc", "id_or_name", "url"),
                                 _parse_tuple(item, 3, "research_route_invalid")))
                       for item in routes],
            "keywords": list(keywords), "output_file": output_file,
        }
    elif any(value not in (None, (), []) for value in research_values):
        _reject("research_contract_wrong_lane")
    return request

def lane_request_from_args(scope, quote, entries):
    lanes = {}
    for raw in entries:
        if not isinstance(raw, str) or "=" not in raw:
            _reject("lane_entry_invalid")
        lane, assignment = (part.strip() for part in raw.split("=", 1))
        if lane not in LANES or lane in lanes:
            _reject("lane_entry_invalid")
        if assignment == "main":
            lanes[lane] = {"main": True}
            continue
        parts = assignment.split(":")
        if len(parts) not in {2, 3} or not all(_nonempty(part) for part in parts):
            _reject("lane_entry_invalid")
        entry = {"company": parts[0], "model": parts[1]}
        if len(parts) == 3:
            entry["effort"] = parts[2]
        lanes[lane] = entry
    return {"scope": scope, "quote": quote, "lanes": lanes}


def _build_job(root, request, contract):
    request = _object(request, "job_request_invalid")
    required = {"lane", "task", "inputs", "outputs", "company", "model", "effort"}
    _keys(request, required, {"id", "research"}, "job_request_invalid")
    lane = request["lane"]
    if not isinstance(lane, str) or lane not in JOB_LANES:
        _reject("job_lane_invalid")
    identifier = request.get("id", str(uuid.uuid4()))
    if not isinstance(identifier, str) or not JOB_ID_RE.fullmatch(identifier):
        _reject("job_id_invalid")
    if not _nonempty(request["task"]):
        _reject("job_task_required")
    if not isinstance(request["company"], str) or request["company"] not in PROVIDERS:
        _reject("job_company_invalid")
    if not isinstance(request["model"], str) or not _nonempty(request["model"]) or "/" in request["model"]:
        _reject("job_model_invalid")
    if request["effort"] is not None and (
            not isinstance(request["effort"], str) or not _nonempty(request["effort"])):
        _reject("job_effort_invalid")
    if not isinstance(request["inputs"], list):
        _reject("job_inputs_invalid")
    inputs = [_input(root, item) for item in request["inputs"]]
    outputs = request["outputs"]
    if not isinstance(outputs, list) or not outputs:
        _reject("job_outputs_required")
    if not all(_relative_output(item) for item in outputs):
        _reject("job_output_invalid")
    if len(set(outputs)) != len(outputs):
        _reject("job_output_duplicate")
    for index, left in enumerate(outputs):
        if any(_outputs_overlap(left, right) for right in outputs[index + 1:]):
            _reject("job_output_overlap")
    has_research = "research" in request
    if lane == "research" and not has_research:
        _reject("research_contract_required")
    if lane != "research" and has_research:
        _reject("research_contract_wrong_lane")
    research = _research(request["research"], outputs) if has_research else None
    jobs_dir = _jobs(root, create=True)
    with _pinned_directory(root, (GUI_DIR, JOBS_DIR), create=False) as directory:
        if any(_outputs_overlap(left, right) for left in outputs
               for right in _existing_outputs_pinned(directory)):
            _reject("job_output_already_owned")
        if (_entry_stat(directory, identifier + ".json") is not None
                or _entry_stat(directory, identifier) is not None):
            _reject("job_id_exists")
    job = {
        "schema": contract["jobSchema"], "id": identifier, "lane": lane,
        "task": request["task"].strip(), "inputs": inputs, "outputs": list(outputs),
        "contractVersion": contract["contractVersion"], "company": request["company"],
        "model": request["model"].strip(),
        "effort": request["effort"].strip() if request["effort"] is not None else None,
    }
    if research is not None:
        job["research"] = research
    return job, jobs_dir


def _validate_lane_request(request, contract):
    request = _object(request, "lane_request_invalid")
    _keys(request, {"scope", "quote", "lanes"}, reason="lane_request_invalid")
    if not isinstance(request["scope"], str) or request["scope"] not in {"once", "continue"}:
        _reject("lane_scope_invalid")
    if not isinstance(request["quote"], str) or not request["quote"].strip():
        _reject("lane_quote_required")
    lanes = request["lanes"]
    if not isinstance(lanes, dict) or not lanes or not set(lanes).issubset(LANES):
        _reject("lane_entries_invalid")
    normalized = {}
    for lane, raw in lanes.items():
        if not isinstance(raw, dict):
            _reject("lane_entry_invalid")
        if "main" in raw:
            if raw != {"main": True}:
                _reject("lane_entry_invalid")
            normalized[lane] = {"main": True}
            continue
        if not {"company", "model"}.issubset(raw) or set(raw) - {"company", "model", "effort"}:
            _reject("lane_entry_invalid")
        company, model = raw.get("company"), raw.get("model")
        effort = raw.get("effort")
        if not isinstance(company, str) or company not in PROVIDERS:
            _reject("lane_company_invalid")
        if not isinstance(model, str) or not _nonempty(model):
            _reject("lane_model_invalid")
        if "effort" in raw and effort is not None and (
                not isinstance(effort, str) or not _nonempty(effort)):
            _reject("lane_effort_invalid")
        normalized[lane] = {"company": company, "model": model.strip()}
        if "effort" in raw:
            normalized[lane]["effort"] = effort.strip() if effort is not None else None
    return {"schema": contract["laneRequestSchema"], "scope": request["scope"],
            "quote": request["quote"], "lanes": normalized}


def _table_cells(line):
    text = line.strip()
    if not text.startswith("|"):
        return None
    text = text[1:-1] if text.endswith("|") else text[1:]
    cells, current = [], []
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip().replace("**", ""))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip().replace("**", ""))
    return cells


def _separator(cells):
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", ""))
                               for cell in cells)


def _tables(text):
    blocks, current = [], []
    for line in text.splitlines():
        cells = _table_cells(line)
        if cells is None:
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(cells)
    if current:
        blocks.append(current)
    result = []
    for block in blocks:
        if len(block) < 2 or not _separator(block[1]) or len(set(block[0])) != len(block[0]):
            continue
        headers = block[0]
        rows = []
        for cells in block[2:]:
            if len(cells) != len(headers):
                _reject("research_result_table_shape_invalid")
            rows.append(dict(zip(headers, cells)))
        result.append((headers, rows))
    return result


def _find_table(tables, required, reason):
    wanted = set(required)
    matches = [(headers, rows) for headers, rows in tables if set(headers) == wanted]
    if len(matches) != 1:
        _reject(reason if not matches else reason.replace("_missing", "_ambiguous"))
    _headers, rows = matches[0]
    return [{name: row[name] for name in required} for row in rows]


def _cell_filled(value):
    value = value.strip()
    return bool(value) and not re.fullmatch(r"<[^<>]+>|(?:\.\.\.|…)", value)


def _require_cells(rows, names):
    for row in rows:
        if not all(_cell_filled(row[name]) for name in names):
            _reject("research_result_required_cell_empty")


def _quantitative(research_type):
    lowered = research_type.lower()
    return any(token in lowered for token in QUANTITATIVE_TYPES)


def validate_research_result(text, research):
    if not isinstance(research, dict) or not _nonempty(research.get("research_type")):
        _reject("research_contract_invalid")
    tables = _tables(text)
    source_rows = _find_table(tables, _SOURCE_COLUMNS, "research_result_source_table_missing")
    unconfirmed_rows = _find_table(tables, _UNCONFIRMED_COLUMNS,
                                   "research_result_unconfirmed_table_missing")
    route_rows = _find_table(tables, _ROUTE_COLUMNS, "research_result_route_table_missing")
    excluded_rows = _find_table(tables, _EXCLUDED_COLUMNS,
                                "research_result_excluded_table_missing")
    unit_tables = [(headers, rows) for headers, rows in tables
                   if set(headers) == set(_UNIT_COLUMNS)]
    if len(unit_tables) > 1:
        _reject("research_result_units_table_ambiguous")
    unit_rows = (_find_table(tables, _UNIT_COLUMNS, "research_result_units_table_missing")
                 if unit_tables else [])
    if _quantitative(research["research_type"]) and not unit_tables:
        _reject("research_result_units_table_missing")
    _require_cells(source_rows, _SOURCE_COLUMNS)
    _require_cells(unconfirmed_rows, _UNCONFIRMED_COLUMNS)
    _require_cells(route_rows, _ROUTE_COLUMNS)
    _require_cells(excluded_rows, _EXCLUDED_COLUMNS)
    _require_cells(unit_rows, _UNIT_COLUMNS)
    question_ids = {question["id"] for question in research["questions"]}
    final_by_question = {}
    for rows, allowed in ((source_rows, SOURCE_STATES),
                          (unconfirmed_rows, UNCONFIRMED_STATES)):
        for row in rows:
            identifier, state = row["질문 ID"], row["질문 최종 상태"]
            if identifier not in question_ids or state not in allowed:
                _reject("research_result_question_status_invalid")
            previous = final_by_question.setdefault(identifier, state)
            if previous != state:
                _reject("research_result_question_status_conflict")
    if final_by_question.keys() != question_ids:
        _reject("research_result_question_status_missing")
    if _quantitative(research["research_type"]):
        unit_questions = {row["질문 ID"] for row in unit_rows}
        measured_questions = {
            identifier for identifier, state in final_by_question.items()
            if state in SOURCE_STATES
        }
        if not measured_questions.issubset(unit_questions):
            _reject("research_result_units_missing_for_evidence")
        if not unit_questions.issubset(measured_questions):
            _reject("research_result_units_question_invalid")
    routes_by_pair = {(route["id_or_name"], route["url"]): index
                      for index, route in enumerate(research["routes"], start=1)}
    seen_routes = set()
    seen_question_routes = set()
    for row in route_rows:
        if row["질문 ID"] not in question_ids:
            _reject("research_result_route_question_invalid")
        try:
            route_number = int(row["경로 번호"])
        except ValueError:
            _reject("research_result_route_number_invalid")
        if row["경로 탐색 결과 (성공 / 실패)"] not in ROUTE_STATES:
            _reject("research_result_route_status_invalid")
        path_text = row["열어본 경로 (기관·자료명·URL)"]
        route_urls = re.findall(r"https?://[^\s<>]+", path_text)
        identity_text = re.sub(r"https?://[^\s<>]+", "", path_text)
        identity_text = " ".join(identity_text.split())
        matches = [(pair, number) for pair, number in routes_by_pair.items()
                   if len(route_urls) == 1
                   and identity_text.count(pair[0]) == 1
                   and (route_urls[0] == pair[1]
                        or route_urls[0].rstrip(".,;:!?)]}") == pair[1])]
        if len(matches) != 1:
            _reject("research_result_route_mismatch")
        route_pair, expected_number = matches[0]
        if route_number != expected_number:
            _reject("research_result_route_number_mismatch")
        question_route = (row["질문 ID"], route_pair)
        if question_route in seen_question_routes:
            _reject("research_result_route_duplicate")
        seen_question_routes.add(question_route)
        seen_routes.add(route_pair)
    if seen_routes != set(routes_by_pair):
        _reject("research_result_route_missing")
    for row in unit_rows:
        if row["질문 ID"] not in question_ids:
            _reject("research_result_units_question_invalid")
    for row in excluded_rows:
        if row["질문 ID"] not in question_ids:
            _reject("research_result_excluded_question_invalid")
    return {"status": "accepted", "questions": len(question_ids),
            "routes": len(routes_by_pair), "tables": 4 + bool(unit_tables)}

def _safe_result_path(out_dir, output_file):
    if not _relative_output(output_file):
        _reject("research_result_path_invalid")
    current = Path(out_dir)
    try:
        for index, part in enumerate(output_file.split("/")):
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                _reject("research_result_path_unsafe")
            if index < len(output_file.split("/")) - 1 and not stat.S_ISDIR(info.st_mode):
                _reject("research_result_path_unsafe")
        if not stat.S_ISREG(current.stat().st_mode):
            _reject("research_result_unavailable")
        return current
    except DelegationError:
        raise
    except OSError:
        _reject("research_result_unavailable")


def check_result(root, job_id):
    root = _root(root)
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        _reject("job_id_invalid")
    jobs_dir = _jobs(root, create=False)
    path = jobs_dir / (job_id + ".json")
    try:
        data, _identity = gg_fs.read_bytes(path)
        record = json.loads(data.decode("utf-8"))
    except (gg_fs.FsError, OSError, UnicodeError, json.JSONDecodeError):
        _reject("job_unavailable")
    if not isinstance(record, dict) or record.get("status") != "completed":
        _reject("job_not_completed")
    spec = record.get("spec", record)
    if (not isinstance(spec, dict) or spec.get("id") != job_id
            or spec.get("lane") != "research"):
        _reject("job_not_research")
    research = spec.get("research")
    if not isinstance(research, dict):
        _reject("job_research_contract_missing")
    outputs = spec.get("outputs")
    if not isinstance(outputs, list) or not all(_relative_output(item) for item in outputs):
        _reject("job_output_invalid")
    research = _research(research, outputs)
    try:
        job_identity = gg_fs.identity(jobs_dir / job_id, kind="directory")
        out_dir = jobs_dir / job_id / "out"
        out_identity = gg_fs.identity(out_dir, kind="directory")
        if out_identity["device"] != job_identity["device"]:
            _reject("job_output_directory_unsafe")
    except DelegationError:
        raise
    except (gg_fs.FsError, OSError):
        _reject("job_output_directory_unsafe")
    result_path = _safe_result_path(out_dir, research["output_file"])
    try:
        text = gg_fs.read_bytes(result_path)[0].decode("utf-8")
    except (gg_fs.FsError, OSError, UnicodeError):
        _reject("research_result_unreadable")
    result = validate_research_result(text, research)
    result["job_id"] = job_id
    result["path"] = str(result_path.relative_to(root))
    return result


def delegate(root, request=None, *, check_job=None, contract_path=None):
    root = _root(root)
    if check_job is not None:
        if request is not None:
            _reject("check_job_request_conflict")
        return check_result(root, check_job)
    if request is None:
        _reject("job_request_required")
    contract = _load_contract(contract_path)
    job, jobs_dir = _build_job(root, request, contract)
    target = jobs_dir / (job["id"] + ".json")
    _publish_new(root, target, _encode(job))
    return {"status": "created", "job_id": job["id"],
            "path": str(target.relative_to(root)), "schema": job["schema"],
            "contractVersion": job["contractVersion"]}


def lanes_propose(root, request, *, contract_path=None):
    root = _root(root)
    contract = _load_contract(contract_path)
    proposal = _validate_lane_request(request, contract)
    gui = _gui(root, create=True)
    target = gui / LANES_REQUEST
    _publish_replace(root, target, _encode(proposal))
    return {"status": "proposed", "path": str(target.relative_to(root)),
            "schema": proposal["schema"], "lanes": len(proposal["lanes"])}
