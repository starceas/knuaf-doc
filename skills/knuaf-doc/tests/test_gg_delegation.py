#!/usr/bin/env python3
"""Offline behavior tests for relay job and lane-request commands."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
GG = SCRIPTS / "gg.py"
sys.path.insert(0, str(SCRIPTS))

ROUTE_NAME = "KOSIS 표 DT_TEST"
ROUTE_URL = "https://example.invalid/table"


def run_gg(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", str(GG), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", env=env)


def workspace(parent):
    root = Path(parent) / "paper"
    root.mkdir()
    return root


def research_args(root, *, job_id="job-1", output="result.md",
                  output_file="result.md"):
    source = root / "sources" / "brief.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("합성 입력 자료\n", encoding="utf-8")
    return [
        "delegate", str(root), "--lane", "research", "--task", "공식 통계 조사",
        "--input-ref", "source-1|sources/brief.md|Ⅱ. 조사 대상",
        "--output", output, "--company", "claude", "--model", "sonnet",
        "--job-id", job_id, "--research-type", "생산량 통계",
        "--question", "Q1|2025년 전국 사과 생산량|연도=2025;지역=전국;단위=톤",
        "--route", f"research-sources.md|{ROUTE_NAME}|{ROUTE_URL}",
        "--keyword", "사과 생산량", "--output-file", output_file,
    ]


def markdown_table(headers, rows=()):
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def research_result(route_name=ROUTE_NAME, route_url=ROUTE_URL, *,
                    source_state="증거 확보", source_present=True,
                    unconfirmed_rows=(), unit_rows=None, route_rows=None):
    source_headers = [
        "질문 ID", "주장 (확인 사실)", "질문 최종 상태", "확인한 원출처", "기준연도",
        "페이지·표·URL", "조회 조건 (지역·기간·필터)", "행·열 위치", "조회 시각",
        "스냅샷 파일 및 해시 (또는 식별 전용 연결)", "열람 상태", "부가 정보", "발견 경로",
    ]
    unconfirmed_headers = [
        "질문 ID", "목표 지표", "질문 최종 상태", "미확인 사유 및 후보 내역",
    ]
    route_headers = [
        "질문 ID", "경로 번호", "열어본 경로 (기관·자료명·URL)",
        "경로 탐색 결과 (성공 / 실패)", "상세 사유",
    ]
    excluded_headers = ["질문 ID", "배제한 출처 (기관/매체명, URL)", "유형", "배제 사유"]
    unit_headers = [
        "질문 ID", "원래 단위", "포장 중량·규격", "유통 단계", "거래 조건", "원래 관측값",
    ]
    tables = [
        markdown_table(source_headers, [[
            "Q1", "2025년 사과 생산량은 100톤", source_state, "통계청", "2025",
            f"통계표 {route_name} {route_url}", "전국·2025", "사과 행·생산량 열",
            "2026-11-05 10:00", "result-source.csv (sha256: " + "a" * 64 + ")",
            "본문 확인", "모집단: 전국", "주문서 1번 경로",
        ]] if source_present else []),
        markdown_table(unconfirmed_headers, unconfirmed_rows),
        markdown_table(route_headers, [[
            "Q1", "1", f"통계청 {route_name} {route_url}", "성공", "원문 표 확인",
        ]] if route_rows is None else route_rows),
        markdown_table(excluded_headers),
        markdown_table(unit_headers, ([
            ["Q1", "톤", "해당 없음", "생산량", "공식 통계", "100톤"],
        ] if unit_rows is None else unit_rows)),
    ]
    return "\n\n".join(tables) + "\n"


class TestDelegationCommands(unittest.TestCase):
    def test_research_job_uses_app_spec_and_hashes_real_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            result = run_gg(*research_args(root))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "created")
            self.assertEqual(output["schema"], 1)
            job = json.loads((root / output["path"]).read_text(encoding="utf-8"))
            source = root / "sources" / "brief.md"
            self.assertEqual(job["contractVersion"], "role-v1")
            self.assertEqual(job["id"], "job-1")
            self.assertEqual(job["lane"], "research")
            self.assertEqual(job["outputs"], ["result.md"])
            self.assertEqual(job["research"]["output_file"], "result.md")
            self.assertEqual(job["inputs"][0]["path"], str(source.resolve()))
            self.assertEqual(job["inputs"][0]["hash"],
                             hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(job["research"]["routes"], [{
                "doc": "research-sources.md", "id_or_name": ROUTE_NAME,
                "url": ROUTE_URL,
            }])
            self.assertEqual(job["research"]["research_type"], "생산량 통계")
            self.assertEqual(job["research"]["questions"], [{
                "id": "Q1", "target_indicator": "2025년 전국 사과 생산량",
                "acceptance_conditions": {
                    "연도": "2025", "지역": "전국", "단위": "톤",
                },
            }])
            self.assertEqual(job["research"]["keywords"], ["사과 생산량"])
            self.assertIsNone(job["effort"])
            self.assertNotIn("spec", job)

        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            created = run_gg(
                "delegate", str(root), "--lane", "writing", "--task", "초안 정리",
                "--output", "draft.md", "--company", "codex", "--model", "gpt-5.6",
                "--effort", "high", "--job-id", "write-1",
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            job = json.loads((root / ".knuaf-gui" / "jobs" / "write-1.json")
                             .read_text(encoding="utf-8"))
            self.assertEqual(set(job), {
                "schema", "id", "lane", "task", "inputs", "outputs",
                "contractVersion", "company", "model", "effort",
            })
            self.assertEqual(job["outputs"], ["draft.md"])
            self.assertEqual(job["effort"], "high")


    def test_research_job_rejects_missing_research_type_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            result = run_gg(
                "delegate", str(root), "--lane", "research", "--task", "조사",
                "--output", "result.md", "--company", "claude", "--model", "sonnet",
                "--job-id", "job-1",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["reason"],
                             "research_contract_required")
            self.assertFalse((root / ".knuaf-gui" / "jobs" / "job-1.json").exists())

    def test_research_result_file_must_be_owned_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            args = research_args(root, output="owned.md", output_file="result.md")
            result = run_gg(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["reason"],
                             "research_output_not_owned")
            self.assertFalse((root / ".knuaf-gui" / "jobs" / "job-1.json").exists())

    def test_output_ownership_rejects_parent_child_collision(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            first = run_gg(
                "delegate", str(root), "--lane", "writing", "--task", "초안",
                "--output", "reports", "--company", "claude", "--model", "sonnet",
                "--job-id", "job-1",
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            second = run_gg(
                "delegate", str(root), "--lane", "repeat", "--task", "교정",
                "--output", "reports/chapter.md", "--company", "codex", "--model", "gpt-5.6",
                "--job-id", "job-2",
            )
            self.assertEqual(second.returncode, 2)
            self.assertEqual(json.loads(second.stdout)["reason"],
                             "job_output_already_owned")
            self.assertFalse((root / ".knuaf-gui" / "jobs" / "job-2.json").exists())

    def test_lane_proposal_preserves_partial_change_schema_and_exact_quote(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            quote = "  검토만 Claude Sonnet high로 바꿔 줘  "
            result = run_gg(
                "lanes-propose", str(root), "--lane-scope", "continue",
                "--quote", quote, "--lane-request", "review=claude:sonnet:high",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            proposal = json.loads((root / ".knuaf-gui" / "lanes-request.json")
                                  .read_text(encoding="utf-8"))
            self.assertEqual(proposal, {
                "schema": 1, "scope": "continue", "quote": quote,
                "lanes": {"review": {
                    "company": "claude", "model": "sonnet", "effort": "high",
                }},
            })

    def test_lane_proposal_rejects_malformed_entries_without_replacing(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            created = run_gg(
                "lanes-propose", str(root), "--lane-scope", "once",
                "--quote", "검토만 Sonnet으로", "--lane-request", "review=claude:sonnet:high",
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            path = root / ".knuaf-gui" / "lanes-request.json"
            original = path.read_bytes()
            for assignment, reason in (
                ("research=claude", "lane_entry_invalid"),
                ("research=other:sonnet", "lane_company_invalid"),
                ("review=claude::high", "lane_entry_invalid"),
            ):
                with self.subTest(assignment=assignment):
                    rejected = run_gg(
                        "lanes-propose", str(root), "--lane-scope", "continue",
                        "--quote", "잘못된 요청", "--lane-request", assignment,
                    )
                    self.assertEqual(rejected.returncode, 2)
                    self.assertEqual(json.loads(rejected.stdout)["reason"], reason)
                    self.assertEqual(path.read_bytes(), original)

    def test_review_main_is_preserved_for_app_lane_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            result = run_gg(
                "lanes-propose", str(root), "--lane-scope", "once",
                "--quote", "검토도 이 대화에서", "--lane-request", "review=main",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            proposal = json.loads((root / ".knuaf-gui" / "lanes-request.json")
                                  .read_text(encoding="utf-8"))
            self.assertEqual(proposal["lanes"], {"review": {"main": True}})

    def test_lane_proposal_preserves_cross_provider_model_for_app_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            result = run_gg(
                "lanes-propose", str(root), "--lane-scope", "once",
                "--quote", "자료 레인 모델 요청", "--lane-request",
                "research=codex:provider/model",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            proposal = json.loads((root / ".knuaf-gui" / "lanes-request.json")
                                  .read_text(encoding="utf-8"))
            self.assertEqual(proposal["lanes"], {
                "research": {"company": "codex", "model": "provider/model"},
            })


    def test_lane_proposal_replaces_previous_valid_request(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            first = run_gg(
                "lanes-propose", str(root), "--lane-scope", "once",
                "--quote", "검토를 Sonnet으로", "--lane-request", "review=claude:sonnet:high",
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            second = run_gg(
                "lanes-propose", str(root), "--lane-scope", "continue",
                "--quote", "자료는 Codex로", "--lane-request", "research=codex:gpt-5.6",
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            proposal = json.loads((root / ".knuaf-gui" / "lanes-request.json")
                                 .read_text(encoding="utf-8"))
            self.assertEqual(proposal["scope"], "continue")
            self.assertEqual(proposal["quote"], "자료는 Codex로")
            self.assertEqual(proposal["lanes"], {
                "research": {"company": "codex", "model": "gpt-5.6"},
            })

    def test_delegate_rejects_duplicate_or_overlapping_outputs_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            for outputs, reason in ((["result.md", "result.md"], "job_output_duplicate"),
                                    (["reports", "reports/chapter.md"], "job_output_overlap")):
                with self.subTest(outputs=outputs):
                    command = [
                        "delegate", str(root), "--lane", "writing", "--task", "초안",
                        "--company", "claude", "--model", "sonnet", "--job-id", "job-1",
                    ]
                    for output in outputs:
                        command.extend(("--output", output))
                    rejected = run_gg(*command)
                    self.assertEqual(rejected.returncode, 2)
                    self.assertEqual(json.loads(rejected.stdout)["reason"], reason)
                    self.assertFalse((root / ".knuaf-gui").exists())

    def test_delegate_rejects_duplicate_question_conditions_and_non_http_routes(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            args = research_args(root)
            question_index = args.index("--question") + 1
            args[question_index] = "Q1|2025년 전국 사과 생산량|연도=2025;연도=2026"
            rejected = run_gg(*args)
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["reason"],
                             "research_question_conditions_invalid")
            self.assertFalse((root / ".knuaf-gui").exists())

            args = research_args(root)
            route_index = args.index("--route") + 1
            args[route_index] = f"research-sources.md|{ROUTE_NAME}|javascript:alert(1)"
            rejected = run_gg(*args)
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["reason"],
                             "research_route_url_invalid")
            self.assertFalse((root / ".knuaf-gui").exists())

    def test_research_result_requires_every_question_state_and_rejects_unowned_route(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            result = run_gg(*research_args(root))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            job_path = root / ".knuaf-gui" / "jobs" / "job-1.json"
            spec = json.loads(job_path.read_text(encoding="utf-8"))
            out_dir = root / ".knuaf-gui" / "jobs" / "job-1" / "out"
            out_dir.mkdir(parents=True)
            result_file = out_dir / "result.md"
            valid = research_result()
            result_file.write_text(valid, encoding="utf-8")
            spec["research"]["appExtension"] = {"verified": True, "version": 2}
            job_path.write_text(json.dumps({"status": "completed", "spec": spec}),
                                encoding="utf-8")

            missing_state = valid.replace("| Q1 | 2025년 사과 생산량은 100톤 |", "| Q2 | 2025년 사과 생산량은 100톤 |")
            result_file.write_text(missing_state, encoding="utf-8")
            rejected = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["reason"],
                             "research_result_question_status_invalid")

            route_table = valid.replace(
                f"{ROUTE_NAME} {ROUTE_URL}", f"{ROUTE_NAME} {ROUTE_URL} https://evil.invalid")
            result_file.write_text(route_table, encoding="utf-8")
            rejected = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["reason"],
                             "research_result_route_mismatch")

            result_file.write_text(valid.replace(
                "| Q1 | 1 |", "| Q1 | 2 |"), encoding="utf-8")
            wrong_number = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(wrong_number.returncode, 2)
            self.assertEqual(json.loads(wrong_number.stdout)["reason"],
                             "research_result_route_number_mismatch")

            result_file.write_text(valid, encoding="utf-8")
            rejected = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)

    def test_research_result_rejects_symlink_in_output_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            result = run_gg(*research_args(root, output="nested/result.md",
                                           output_file="nested/result.md"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            job_path = root / ".knuaf-gui" / "jobs" / "job-1.json"
            spec = json.loads(job_path.read_text(encoding="utf-8"))
            job_dir = root / ".knuaf-gui" / "jobs" / "job-1"
            out_dir = job_dir / "out"
            out_dir.mkdir(parents=True)
            external = root / "external"
            external.mkdir()
            (out_dir / "nested").symlink_to(external, target_is_directory=True)
            job_path.write_text(json.dumps({"status": "completed", "spec": spec}),
                                encoding="utf-8")
            rejected = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["reason"],
                             "research_result_path_unsafe")

    def test_job_output_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            result = run_gg(
                "delegate", str(root), "--lane", "writing", "--task", "초안",
                "--output", "../outside.md", "--company", "claude", "--model", "sonnet",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["reason"], "job_output_invalid")
            self.assertFalse((root / ".knuaf-gui").exists())

    def test_parent_swap_at_atomic_publish_never_writes_outside_workspace(self):
        import gg_delegation as delegation
        from unittest.mock import patch

        real_rename = delegation.os.rename

        def swap_parent(root, gui, outside):
            try:
                real_rename(gui, root / ".knuaf-gui-saved")
            except OSError:
                if sys.platform == "win32":
                    return False
                raise
            gui.symlink_to(outside, target_is_directory=True)
            return True

        for command in ("delegate", "lanes-propose"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as td:
                root = workspace(td)
                gui = root / ".knuaf-gui"
                gui.mkdir()
                outside = Path(td) / "outside"
                outside.mkdir()
                if command == "delegate":
                    (gui / "jobs").mkdir()
                    (outside / "jobs").mkdir()
                    publish = (real_rename if sys.platform == "win32"
                               else delegation._rename_noreplace_at)
                    swap_observation = []

                    def swap_then_publish(*args, **kwargs):
                        swap_observation.append(swap_parent(root, gui, outside))
                        return publish(*args, **kwargs)

                    request = {
                        "id": "job-1", "lane": "writing", "task": "초안",
                        "inputs": [], "outputs": ["draft.md"],
                        "company": "claude", "model": "sonnet", "effort": None,
                    }
                    with patch.object(delegation.os if sys.platform == "win32" else delegation,
                                      "rename" if sys.platform == "win32" else "_rename_noreplace_at",
                                      swap_then_publish):
                        if sys.platform == "win32":
                            delegation.delegate(root, request)
                        else:
                            with self.assertRaisesRegex(
                                    delegation.DelegationError,
                                    "delegation_directory_unsafe"):
                                delegation.delegate(root, request)
                    self.assertEqual(len(swap_observation), 1)
                    if sys.platform == "win32":
                        self.assertEqual(swap_observation, [False])
                        self.assertTrue((gui / "jobs" / "job-1.json").is_file())
                    else:
                        self.assertEqual(swap_observation, [True])
                        self.assertFalse((root / ".knuaf-gui-saved" / "jobs" /
                                          "job-1.json").exists())
                    self.assertFalse((outside / "jobs" / "job-1.json").exists())
                else:
                    (gui / "lanes-request.json").write_text(
                        "paper-sentinel", encoding="utf-8")
                    (outside / "lanes-request.json").write_text(
                        "outside-sentinel", encoding="utf-8")
                    proposal = {
                        "scope": "once", "quote": "검토만 Sonnet으로",
                        "lanes": {"review": {
                            "company": "claude", "model": "sonnet",
                            "effort": "high",
                        }},
                    }
                    original_replace = delegation.os.replace
                    swap_observation = []

                    def swap_then_replace(source, destination, **kwargs):
                        swap_observation.append(swap_parent(root, gui, outside))
                        return original_replace(source, destination, **kwargs)

                    with patch.object(delegation.os, "replace", swap_then_replace):
                        if sys.platform == "win32":
                            delegation.lanes_propose(root, proposal)
                        else:
                            with self.assertRaisesRegex(
                                    delegation.DelegationError,
                                    "delegation_directory_unsafe"):
                                delegation.lanes_propose(root, proposal)
                    self.assertEqual(len(swap_observation), 1)
                    if sys.platform == "win32":
                        self.assertEqual(swap_observation, [False])
                        published = gui / "lanes-request.json"
                        self.assertEqual(json.loads(published.read_text(encoding="utf-8")), {
                            "schema": 1, "scope": "once", "quote": "검토만 Sonnet으로",
                            "lanes": {"review": {
                                "company": "claude", "model": "sonnet", "effort": "high",
                            }},
                        })
                    else:
                        self.assertEqual(swap_observation, [True])
                        self.assertFalse((root / ".knuaf-gui-saved" /
                                          "lanes-request.json").exists())
                    self.assertEqual((outside / "lanes-request.json").read_text(
                        encoding="utf-8"), "outside-sentinel")


    def test_directory_moved_outside_before_write_leaves_no_payload(self):
        import gg_delegation as delegation
        from unittest.mock import patch

        real_open = delegation.os.open
        real_write = delegation.os.write
        for command in ("delegate", "lanes-propose"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as td:
                root = workspace(td)
                gui = root / ".knuaf-gui"
                (gui / "jobs").mkdir(parents=True)
                outside = Path(td) / "outside"
                outside.mkdir()
                moved = outside / "moved-gui"
                observed_sizes = []
                writes = []

                def move_before_temp_open(path, flags, *args, **kwargs):
                    if isinstance(path, str) and path.endswith(".tmp"):
                        gui.rename(moved)
                        gui.mkdir()
                        fd = real_open(path, flags, *args, **kwargs)
                        observed_sizes.append(os.fstat(fd).st_size)
                        return fd
                    return real_open(path, flags, *args, **kwargs)

                def record_write(fd, data):
                    writes.append(bytes(data))
                    return real_write(fd, data)

                request = {
                    "id": "job-1", "lane": "writing", "task": "초안",
                    "inputs": [], "outputs": ["draft.md"],
                    "company": "claude", "model": "sonnet", "effort": None,
                }
                proposal = {
                    "scope": "once", "quote": "검토만 Sonnet으로",
                    "lanes": {"review": {
                        "company": "claude", "model": "sonnet", "effort": "high",
                    }},
                }
                with patch.object(delegation.os, "open", move_before_temp_open), \
                        patch.object(delegation.os, "write", record_write):
                    with self.assertRaisesRegex(
                            delegation.DelegationError,
                            "delegation_directory_unsafe"):
                        if command == "delegate":
                            delegation.delegate(root, request)
                        else:
                            delegation.lanes_propose(root, proposal)

                self.assertEqual(observed_sizes, [0])
                self.assertEqual(writes, [])
                self.assertEqual(list(moved.rglob("*.tmp")), [])
                self.assertFalse((moved / "jobs" / "job-1.json").exists())
                self.assertFalse((moved / "lanes-request.json").exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX file-move race")
    def test_opened_temp_moved_before_write_reports_pending_without_payload(self):
        import gg_delegation as delegation
        from unittest.mock import patch

        real_open, real_write = os.open, os.write
        for command in ("delegate", "lanes-propose"):
            for replacement in ("missing", "file", "symlink"):
                with self.subTest(command=command, replacement=replacement), \
                        tempfile.TemporaryDirectory() as td:
                    root = workspace(td)
                    gui = root / ".knuaf-gui"
                    target = gui / "jobs" if command == "delegate" else gui
                    target.mkdir(parents=True)
                    outside = Path(td) / "outside"
                    outside.mkdir()
                    sentinel = outside / "sentinel"
                    sentinel.write_bytes(b"OUTSIDE")
                    moved = outside / "moved.tmp"
                    names, writes = [], []

                    def move_after_open(path, flags, *args, **kwargs):
                        fd = real_open(path, flags, *args, **kwargs)
                        if isinstance(path, str) and path.endswith(".tmp"):
                            original = target / path
                            original.rename(moved)
                            names.append(original)
                            if replacement == "file":
                                original.write_bytes(b"UNRELATED")
                            elif replacement == "symlink":
                                original.symlink_to(sentinel)
                        return fd

                    def record_write(fd, data):
                        writes.append(bytes(data))
                        return real_write(fd, data)

                    with patch.object(delegation.os, "open", move_after_open), \
                            patch.object(delegation.os, "write", record_write):
                        with self.assertRaises(delegation.DelegationError) as caught:
                            self._invoke_synthetic_delegation(command, root)
                    self.assertEqual(writes, [])
                    self.assertEqual(moved.read_bytes(), b"")
                    self.assertEqual(sentinel.read_bytes(), b"OUTSIDE")
                    self.assertEqual(str(caught.exception),
                                     "delegation_write_cleanup_pending:" +
                                     moved.resolve().as_posix())
                    if replacement == "file":
                        self.assertEqual(names[0].read_bytes(), b"UNRELATED")
                    elif replacement == "symlink":
                        self.assertTrue(names[0].is_symlink())
                    else:
                        self.assertFalse(names[0].exists())
                    final = "job-1.json" if command == "delegate" else "lanes-request.json"
                    self.assertFalse((target / final).exists())

    def _invoke_synthetic_delegation(self, command, root):
        import gg_delegation as delegation

        if command == "delegate":
            return delegation.delegate(root, {
                "id": "job-1", "lane": "writing", "task": "SYNTHETIC-STUDENT-CONTENT",
                "inputs": [], "outputs": ["draft.md"],
                "company": "claude", "model": "sonnet", "effort": None,
            })
        return delegation.lanes_propose(root, {
            "scope": "once", "quote": "SYNTHETIC-STUDENT-CONTENT",
            "lanes": {"review": {"company": "claude", "model": "sonnet", "effort": "high"}},
        })

    def _assert_publish_move_cleanup(self, command, *, existing=False):
        import gg_delegation as delegation
        from unittest.mock import patch

        real_publish = os.replace if existing else delegation._rename_noreplace_at
        real_unlink = os.unlink
        for cleanup in ("removed", "denied", "detached", "replaced"):
            with self.subTest(command=command, existing=existing, cleanup=cleanup), \
                    tempfile.TemporaryDirectory() as td:
                root = workspace(td)
                gui = root / ".knuaf-gui"
                (gui / "jobs").mkdir(parents=True)
                name = "job-1.json" if command == "delegate" else "lanes-request.json"
                target = gui / "jobs" if command == "delegate" else gui
                if existing:
                    (target / name).write_bytes(b"ORIGINAL")
                outside = Path(td) / "outside"
                outside.mkdir()
                sentinel = outside / name
                sentinel.write_bytes(b"OUTSIDE")
                moved = outside / "moved-gui"
                published = moved / "jobs" / name if command == "delegate" else moved / name
                detached = outside / "detached.json"
                observed = []

                def move_then_publish(*args, **kwargs):
                    gui.rename(moved)
                    gui.mkdir()
                    if existing:
                        self.assertEqual(published.read_bytes(), b"ORIGINAL")
                    result = real_publish(*args, **kwargs)
                    observed.append(json.loads(published.read_text(encoding="utf-8")))
                    if cleanup in ("detached", "replaced"):
                        published.rename(detached)
                        if cleanup == "replaced":
                            published.write_bytes(b"UNRELATED")
                    return result

                def deny_owned_unlink(path, *args, **kwargs):
                    if cleanup == "denied" and path == name and "dir_fd" in kwargs:
                        raise PermissionError("synthetic cleanup denied")
                    return real_unlink(path, *args, **kwargs)

                owner = delegation.os if existing else delegation
                method = "replace" if existing else "_rename_noreplace_at"
                with patch.object(owner, method, move_then_publish), \
                        patch.object(delegation.os, "unlink", deny_owned_unlink):
                    with self.assertRaises(delegation.DelegationError) as caught:
                        self._invoke_synthetic_delegation(command, root)
                field = "task" if command == "delegate" else "quote"
                self.assertEqual([row[field] for row in observed], ["SYNTHETIC-STUDENT-CONTENT"])
                self.assertEqual(sentinel.read_bytes(), b"OUTSIDE")
                self.assertEqual(list(moved.rglob("*.tmp")), [])
                self.assertEqual(list(gui.iterdir()), [])
                if cleanup == "removed":
                    self.assertEqual(str(caught.exception), "delegation_directory_unsafe")
                    self.assertFalse(published.exists())
                else:
                    residual = published if cleanup == "denied" else detached
                    self.assertEqual(str(caught.exception),
                                     "delegation_write_cleanup_pending:" +
                                     residual.resolve().as_posix())
                    self.assertEqual(json.loads(residual.read_text(encoding="utf-8"))[field],
                                     "SYNTHETIC-STUDENT-CONTENT")
                    if cleanup == "replaced":
                        self.assertEqual(published.read_bytes(), b"UNRELATED")
                    elif cleanup == "detached":
                        self.assertFalse(published.exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX directory-move race")
    def test_delegate_publish_move_cleans_owned_result_or_reports_location(self):
        self._assert_publish_move_cleanup("delegate")

    @unittest.skipIf(sys.platform == "win32", "POSIX directory-move race")
    def test_new_lane_publish_move_cleans_owned_result_or_reports_location(self):
        self._assert_publish_move_cleanup("lanes-propose")

    @unittest.skipIf(sys.platform == "win32", "POSIX directory-move race")
    def test_existing_lane_replace_move_cleans_owned_result_or_reports_location(self):
        self._assert_publish_move_cleanup("lanes-propose", existing=True)

    def test_research_result_rejects_route_status_outside_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            created = run_gg(*research_args(root))
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            job_path = root / ".knuaf-gui" / "jobs" / "job-1.json"
            spec = json.loads(job_path.read_text(encoding="utf-8"))
            spec["status"] = "completed"
            job_path.write_text(json.dumps(spec), encoding="utf-8")
            out_dir = root / ".knuaf-gui" / "jobs" / "job-1" / "out"
            out_dir.mkdir(parents=True)
            result_file = out_dir / "result.md"

            for route_status in ("성공", "실패"):
                with self.subTest(route_status=route_status):
                    result_file.write_text(research_result(route_rows=[[
                        "Q1", "1", f"통계청 {ROUTE_NAME} {ROUTE_URL}",
                        route_status, "경로 확인",
                    ]]), encoding="utf-8")
                    accepted = run_gg("delegate", str(root),
                                      "--check-job", "job-1")
                    self.assertEqual(accepted.returncode, 0,
                                     accepted.stdout + accepted.stderr)
                    self.assertEqual(json.loads(accepted.stdout)["status"],
                                     "accepted")

            result_file.write_text(research_result(route_rows=[[
                "Q1", "1", f"통계청 {ROUTE_NAME} {ROUTE_URL}",
                "미실행", "경로를 열지 않음",
            ]]), encoding="utf-8")
            rejected = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["reason"],
                             "research_result_route_status_invalid")
    def test_delegate_partial_write_failure_removes_owned_temporary(self):
        import errno
        import gg_delegation as delegation
        import gg_fs
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            real_write = gg_fs.os.write
            calls = 0

            def disk_full(fd, data):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_write(fd, data[:16])
                raise OSError(errno.ENOSPC, "synthetic disk full")

            request = {
                "id": "job-1", "lane": "writing", "task": "초안",
                "inputs": [], "outputs": ["draft.md"],
                "company": "claude", "model": "sonnet", "effort": None,
            }
            with patch.object(gg_fs.os, "write", disk_full):
                with self.assertRaisesRegex(
                        delegation.DelegationError, "delegation_write_failed"):
                    delegation.delegate(root, request)
            jobs = root / ".knuaf-gui" / "jobs"
            self.assertEqual(list(jobs.iterdir()), [])
            self.assertFalse((jobs / "job-1.json").exists())

    def test_research_result_matches_route_id_even_when_id_is_in_url(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            route_id = "DT_1J22003"
            route_url = "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1J22003"
            args = research_args(root)
            route_index = args.index("--route") + 1
            args[route_index] = f"research-sources.md|{route_id}|{route_url}"
            result = run_gg(*args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            job_path = root / ".knuaf-gui" / "jobs" / "job-1.json"
            spec = json.loads(job_path.read_text(encoding="utf-8"))
            spec["status"] = "completed"
            job_path.write_text(json.dumps(spec), encoding="utf-8")
            out_dir = root / ".knuaf-gui" / "jobs" / "job-1" / "out"
            out_dir.mkdir(parents=True)
            result_file = out_dir / "result.md"
            result_file.write_text(research_result(route_id, route_url),
                                   encoding="utf-8")
            accepted = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertEqual(json.loads(accepted.stdout)["status"], "accepted")
            result_file.write_text(
                research_result(route_id, "https://evil.invalid", route_rows=[[
                    "Q1", "1", f"통계청 {route_id} https://evil.invalid",
                    "성공", "원문 표 확인",
                ]]), encoding="utf-8")
            rejected = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["reason"],
                             "research_result_route_mismatch")

    def test_research_result_requires_unit_rows_only_for_measured_questions(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            result = run_gg(*research_args(root))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            job_path = root / ".knuaf-gui" / "jobs" / "job-1.json"
            spec = json.loads(job_path.read_text(encoding="utf-8"))
            spec["status"] = "completed"
            job_path.write_text(json.dumps(spec), encoding="utf-8")
            out_dir = root / ".knuaf-gui" / "jobs" / "job-1" / "out"
            out_dir.mkdir(parents=True)
            result_file = out_dir / "result.md"
            incomplete = research_result(unit_rows=[])
            result_file.write_text(incomplete, encoding="utf-8")
            rejected = run_gg("delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(json.loads(rejected.stdout)["reason"],
                             "research_result_units_missing_for_evidence")

            candidate = research_result(
                source_state="조건 불일치",
                unit_rows=[["Q1", "톤", "해당 없음", "생산량", "전국 외", "100톤"]])
            result_file.write_text(candidate, encoding="utf-8")
            accepted_candidate = run_gg(
                "delegate", str(root), "--check-job", "job-1")
            self.assertEqual(accepted_candidate.returncode, 0,
                             accepted_candidate.stdout + accepted_candidate.stderr)

            candidate_without_numbers = research_result(
                source_state="조건 불일치",
                unit_rows=[["Q1", "톤", "해당 없음", "생산량", "전국 외", "100톤"]])
            candidate_without_numbers = candidate_without_numbers.replace(
                "2025년 사과 생산량은 100톤",
                "전국 수치는 미공표이며 경북 생산량 후보만 확인")
            result_file.write_text(candidate_without_numbers, encoding="utf-8")
            accepted_candidate = run_gg(
                "delegate", str(root), "--check-job", "job-1")
            self.assertEqual(accepted_candidate.returncode, 0,
                             accepted_candidate.stdout + accepted_candidate.stderr)
            self.assertEqual(json.loads(accepted_candidate.stdout)["status"],
                             "accepted")

            candidate_without_unit_row = research_result(
                source_state="조건 불일치", unit_rows=[]).replace(
                    "2025년 사과 생산량은 100톤",
                    "경북 사과 생산량은 백 톤")
            result_file.write_text(candidate_without_unit_row, encoding="utf-8")
            rejected_candidate = run_gg(
                "delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected_candidate.returncode, 2)
            self.assertEqual(json.loads(rejected_candidate.stdout)["reason"],
                             "research_result_units_missing_for_evidence")
            for state, reason in (
                    ("자료 없음", "공식 경로와 별칭 2종에서 자료 없음"),
                    ("접근 실패", "공식 경로 서버 오류로 접근 불가")):
                with self.subTest(unconfirmed_state=state):
                    no_evidence = research_result(
                        source_present=False,
                        unconfirmed_rows=[["Q1", "2025년 전국 사과 생산량",
                                           state, reason]],
                        unit_rows=[],
                        route_rows=[["Q1", "1",
                                     f"통계청 {ROUTE_NAME} {ROUTE_URL}",
                                     "실패", reason]])
                    result_file.write_text(no_evidence, encoding="utf-8")
                    accepted_unconfirmed = run_gg(
                        "delegate", str(root), "--check-job", "job-1")
                    self.assertEqual(
                        accepted_unconfirmed.returncode, 0,
                        accepted_unconfirmed.stdout + accepted_unconfirmed.stderr)
                    self.assertEqual(
                        json.loads(accepted_unconfirmed.stdout)["status"],
                        "accepted")
            evidence_for_unobserved = research_result(
                source_present=False,
                unconfirmed_rows=[["Q1", "2025년 전국 사과 생산량", "자료 없음",
                                   "공식 경로와 별칭 2종에서 자료 없음"]],
                unit_rows=[["Q1", "톤", "해당 없음", "생산량", "전국", "미관측"]],
                route_rows=[["Q1", "1", f"통계청 {ROUTE_NAME} {ROUTE_URL}",
                             "실패", "공식 자료에 없음"]])
            result_file.write_text(evidence_for_unobserved, encoding="utf-8")
            rejected_unobserved = run_gg(
                "delegate", str(root), "--check-job", "job-1")
            self.assertEqual(rejected_unobserved.returncode, 2)
            self.assertEqual(
                json.loads(rejected_unobserved.stdout)["reason"],
                "research_result_units_question_invalid")

if __name__ == "__main__":
    unittest.main()
