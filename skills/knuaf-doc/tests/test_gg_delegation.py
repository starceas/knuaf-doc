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


def research_result(route_name=ROUTE_NAME, route_url=ROUTE_URL):
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
            "Q1", "2025년 사과 생산량은 100톤", "증거 확보", "통계청", "2025",
            f"통계표 {route_name} {route_url}", "전국·2025", "사과 행·생산량 열",
            "2026-11-05 10:00", "result-source.csv (sha256: " + "a" * 64 + ")",
            "본문 확인", "모집단: 전국", "주문서 1번 경로",
        ]]),
        markdown_table(unconfirmed_headers),
        markdown_table(route_headers, [[
            "Q1", "1", f"통계청 {route_name} {route_url}", "성공", "원문 표 확인",
        ]]),
        markdown_table(excluded_headers),
        markdown_table(unit_headers, [[
            "Q1", "톤", "해당 없음", "생산량", "공식 통계", "100톤",
        ]]),
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

if __name__ == "__main__":
    unittest.main()
