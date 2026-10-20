"""P1 GUI-inheritance fixes — body defects judged '올림' in JUDGE-A/JUDGE-B.

Every test exercises the deployed runtime and a produced artifact or an
injected failure; nothing asserts source text.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._harness import (
    ContractCase,
    SCRIPTS,
    bind_major,
    claim_of,
    fact_op,
    runtime,
    section_claim,
    section_op,
    source_op,
    write_text,
)


def _seed(root):
    """One source+fact+section triple at revision 1."""
    write_text(root, "answer.txt", "농장명: 행복농장\n")
    write_text(root, "intro.md", "농장명은 행복농장이다.\n")
    fact = fact_op("farm_name", "farm_name", "행복농장", None, claim_id="c1")
    ops = [
        source_op(claims=claim_of(fact["value"])),
        fact,
        section_op(
            "intro", "머리말", "intro.md",
            claims=[section_claim(fact["value"], "농장명은 행복농장이다")]),
    ]
    return runtime("gg_core").apply(
        root, {"request_id": "seed", "ops": ops}, 0)


def _cli(*args, cwd=None, env_extra=None):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-B", *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd) if cwd else None, env=env, timeout=120)


class ExportEmptyBodyTests(ContractCase):
    """JUDGE-A #1: an empty export must not be reported as generated."""

    def test_export_refuses_a_project_with_no_sections(self):
        core = runtime("gg_core")
        root = self.make_project()
        bind_major(root)
        before = self._tree_bytes(root)
        with self.assertRaises(core.OperationError) as cm:
            core.export(root, "draft", major_id="specialty_crops")
        self.assertEqual("empty_export_body", cm.exception.result["reason"])
        self.assertEqual("not_committed",
                         cm.exception.result["commit_state"])
        self.assertIn("내보낼 본문이 없음", str(cm.exception))
        self.assertEqual(before, self._tree_bytes(root))

    def test_export_allows_a_titles_only_outline(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "sections/01.md", "# Ⅰ. 머리말\n")
        section_op_value = section_op("s1", "Ⅰ. 머리말", "sections/01.md")
        core.apply(root, {"request_id": "outline",
                          "ops": [section_op_value]},
                   core.load(root)["revision"])
        bind_major(root)
        value = core.export(root, "draft", major_id="specialty_crops")
        self.assertEqual("generated", value["status"])
        self.assertIn("Ⅰ. 머리말",
                      Path(value["path"]).read_text(encoding="utf-8"))

    def test_export_still_works_with_real_content(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        value = core.export(root, "draft", major_id="specialty_crops")
        self.assertEqual("generated", value["status"])
        self.assertGreater(Path(value["path"]).stat().st_size, 0)


class MachineFieldIdTests(ContractCase):
    """JUDGE-A #4: a machine field_id is not evidence the body is wrong."""

    def _project(self, field_id):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "매출액: 1,000 천원\n")
        write_text(root, "intro.md", "농장 매출은 1,000 천원이다.\n")
        fact = fact_op("revenue", field_id, "1000", "천원",
                       value_type="decimal", period="2026", scope="farm",
                       claim_id="c1")
        core.apply(root, {
            "request_id": "seed",
            "ops": [
                source_op(claims=claim_of(fact["value"])),
                fact,
                section_op("intro", "머리말", "intro.md"),
            ],
        }, 0)
        return core, root

    def _rows(self, root):
        core = runtime("gg_core")
        p = core.load(root)
        return [r for r in core.checks(root, p)
                if r["check_id"] == "body_finance_crosscheck"]

    def test_machine_field_id_is_blocked_not_blamed_on_the_body(self):
        core, root = self._project("finance.own_capital")
        rows = self._rows(root)
        self.assertEqual(1, len(rows), rows)
        self.assertEqual("blocked", rows[0]["status"], rows[0])
        self.assertIn("기계용 식별자", rows[0]["reason"])

    def test_a_real_missing_term_stays_a_failure(self):
        core, root = self._project("매출액")
        write_text(root, "intro.md", "다른 문장만 있다.\n")
        rows = self._rows(root)
        self.assertEqual(1, len(rows), rows)
        self.assertEqual("fail", rows[0]["status"], rows[0])
        self.assertEqual("본문에 재무 항목 없음", rows[0]["reason"])


class FormulaRangeTests(ContractCase):
    """JUDGE-A #6: n-ary sums/products are checked; unexpressible formulas
    are a tool limit, not a data failure."""

    def _project(self, op, count, value, *, unit_reason="합계 단위는 천원"):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "수치 원답변\n")
        write_text(root, "intro.md", "본문\n")
        ops = [source_op()]
        ids = []
        for i in range(count):
            fid = "f%d" % i
            ids.append(fid)
            ops.append(fact_op(fid, "field.%d" % i, "1000", "천원",
                               value_type="decimal", period="2026",
                               scope="farm"))
        formula = {"op": op}
        if unit_reason is not None:
            formula["unit_reason"] = unit_reason
        derived = fact_op("total", "field.total", value, "천원",
                          value_type="decimal", period="2026", scope="farm",
                          kind="derived")
        derived["value"]["formula"] = formula
        derived["value"]["input_refs"] = [
            {"id": fid, "revision": 1} for fid in ids]
        ops.append(derived)
        ops.append(section_op("intro", "머리말", "intro.md"))
        core.apply(root, {"request_id": "seed", "ops": ops}, 0)
        return core, root

    def _row(self, core, root):
        """The calculation row for ``total``; None when it verified clean."""
        p = core.load(root)
        rows = [r for r in core.checks(root, p)
                if r["check_id"] == "calculation" and r["target"] == "total"]
        self.assertLessEqual(len(rows), 1, rows)
        return rows[0] if rows else None

    def test_n_ary_sum_is_verified(self):
        core, root = self._project("add", 10, "10000")
        self.assertIsNone(self._row(core, root))

    def test_n_ary_product_is_verified(self):
        core, root = self._project("multiply", 3, "1000000000")
        self.assertIsNone(self._row(core, root))

    def test_a_wrong_n_ary_sum_stays_a_failure(self):
        core, root = self._project("add", 10, "9999")
        row = self._row(core, root)
        self.assertEqual("fail", row["status"])
        self.assertEqual("계산 결과 불일치", row["reason"])

    def test_formulas_the_tool_cannot_express_are_blocked_not_blamed(self):
        core, root = self._project("pmt", 2, "1")
        row = self._row(core, root)
        self.assertEqual("blocked", row["status"])
        self.assertIn("미지원 연산", row["reason"])
        core, root = self._project("divide", 3, "1")
        row = self._row(core, root)
        self.assertEqual("blocked", row["status"])
        self.assertIn("피연산자 3개", row["reason"])

    def test_missing_unit_reason_stays_a_failure(self):
        core, root = self._project("add", 3, "3000", unit_reason=None)
        row = self._row(core, root)
        self.assertEqual("fail", row["status"])
        self.assertEqual("단위 근거 필요", row["reason"])

    def test_a_blocked_formula_still_blocks_the_submission_gate(self):
        core, root = self._project("pmt", 2, "1")
        p = core.load(root)
        rows = core.checks(root, p)
        blocked = [r for r in rows if r["status"] == "blocked"
                   and r["severity"] == "error"]
        self.assertTrue(blocked, rows)


class PaperBodyTests(ContractCase):
    """JUDGE-B #1/#2: a merged body replaces the generated Ⅰ–Ⅵ skeleton
    while the frontmatter stays; gg_school_paper.py is not modified."""

    STUDENT_BODY = (
        "# Ⅰ. 머리말\n\n학생이 쓴 머리말.\n\n"
        "# Ⅱ. 농장현황\n\n2026 목차로 쓴 농장현황.\n\n"
        "# Ⅶ. 참고문헌\n\n참고문헌.\n"
    )

    def _setup(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        spec = root / "paper-spec.json"
        spec.write_text(json.dumps(
            {"author": "합성", "writing_year": 2026, "years": 5,
             "school_profile": {"mode": "school", "school": "합성대학교",
                                "department": "특용작물학과"}},
            ensure_ascii=False), encoding="utf-8")
        return core, root, spec

    def test_body_replaces_the_generated_skeleton_and_keeps_frontmatter(self):
        core, root, spec = self._setup()
        value = core.paper(
            root, "paper-spec.json", spec.read_bytes(), "build/body.md",
            major_id="specialty_crops", body_markdown=self.STUDENT_BODY)
        self.assertEqual("generated", value["status"])
        text = (root / "build/body.md").read_text(encoding="utf-8")
        self.assertIn("겉표지", text)
        self.assertIn("목차", text)
        self.assertIn("Ⅱ. 농장현황", text)
        self.assertIn("2026 목차로 쓴 농장현황", text)
        self.assertNotIn("Ⅲ. 영농계획수립", text)
        # The request records the body hash it actually published.
        receipt = json.loads(
            (root / "build" / str(core.load(root)["revision"]) / "paper"
             / ".publication.json").read_text(encoding="utf-8"))
        self.assertEqual(64, len(receipt["request"]["body_sha256"]))

    def test_empty_body_is_refused_without_output(self):
        core, root, spec = self._setup()
        for bad in ("", "   \n", "\ufeff  \n"):
            with self.assertRaises(core.OperationError) as cm:
                core.paper(
                    root, "paper-spec.json", spec.read_bytes(),
                    "build/body.md", major_id="specialty_crops",
                    body_markdown=bad)
            self.assertEqual("invalid_spec",
                             cm.exception.result["reason"])
        self.assertFalse((root / "build/body.md").exists())

    def test_body_must_start_at_the_first_chapter(self):
        core, root, spec = self._setup()
        with self.assertRaises(core.OperationError) as cm:
            core.paper(
                root, "paper-spec.json", spec.read_bytes(),
                "build/body.md", major_id="specialty_crops",
                body_markdown="# Ⅱ. 농장현황\n\n본문\n")
        self.assertEqual("invalid_spec", cm.exception.result["reason"])
        self.assertFalse((root / "build/body.md").exists())

    def test_paper_without_a_body_is_unchanged(self):
        core, root, spec = self._setup()
        core.paper(root, "paper-spec.json", spec.read_bytes(),
                   "build/plain.md", major_id="specialty_crops")
        text = (root / "build/plain.md").read_text(encoding="utf-8")
        self.assertIn("Ⅲ. 영농계획수립", text)

    def test_cli_passes_the_body_file(self):
        core, root, spec = self._setup()
        body = root / "student-body.md"
        body.write_text(self.STUDENT_BODY, encoding="utf-8")
        result = _cli(SCRIPTS / "gg.py", "paper", root,
                      "--input", "paper-spec.json", "--body",
                      "student-body.md", "--out", "build/cli-body.md",
                      "--major", "specialty_crops")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        text = (root / "build/cli-body.md").read_text(encoding="utf-8")
        self.assertIn("2026 목차로 쓴 농장현황", text)
        self.assertNotIn("Ⅲ. 영농계획수립", text)


class AtomicRetryTests(ContractCase):
    """JUDGE-B #4: a Windows sharing violation is retried, others are not."""

    def test_permission_error_is_retried_on_windows(self):
        core = runtime("gg_core")
        root = self.make_project()
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise PermissionError("sharing violation")
            return real(src, dst)

        with mock.patch.object(core.os, "replace", side_effect=flaky), \
                mock.patch.object(core.sys, "platform", "win32"), \
                mock.patch.object(core.time, "sleep", lambda _s: None):
            core.atomic(root / "state.txt", b"payload")
        self.assertEqual(2, len(calls))
        self.assertEqual(b"payload", (root / "state.txt").read_bytes())

    def test_other_platforms_raise_immediately(self):
        core = runtime("gg_core")
        root = self.make_project()
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            raise PermissionError("denied")

        with mock.patch.object(core.os, "replace", side_effect=flaky), \
                mock.patch.object(core.sys, "platform", "darwin"):
            with self.assertRaises(PermissionError):
                core.atomic(root / "state.txt", b"payload")
        self.assertEqual(1, len(calls))
        self.assertFalse((root / "state.txt").exists())


class CommandsGuardTests(ContractCase):
    """JUDGE-B #9: a missing or empty target is blocked, not a green pass."""

    def test_a_missing_target_is_blocked_json(self):
        result = _cli(SCRIPTS / "lint_format.py", "/no/such/dir-xyz")
        self.assertEqual(2, result.returncode, result.stdout)
        rows = json.loads(result.stdout)
        self.assertEqual("blocked", rows[0]["status"])
        self.assertIn("대상 경로가 없음", rows[0]["reason"])
        self.assertNotIn("Traceback", result.stderr)

    def test_an_empty_target_is_blocked(self):
        tmp = tempfile.TemporaryDirectory(prefix="p1-empty-")
        self.addCleanup(tmp.cleanup)
        empty = Path(tmp.name) / "empty"
        empty.mkdir()
        (empty / "sections").mkdir()
        for target in (empty, empty / "blank.md"):
            if target.suffix:
                target.write_text("   \n", encoding="utf-8")
            result = _cli(SCRIPTS / "lint_format.py", target)
            self.assertEqual(2, result.returncode, result.stdout)
            rows = json.loads(result.stdout)
            self.assertEqual("blocked", rows[0]["status"], rows)
            self.assertIn("검사할 본문이 없음", rows[0]["reason"])


class DoctorDepsTests(ContractCase):
    """JUDGE-B #11: doctor answers from the project venv, not this CLI."""

    def test_doctor_reports_the_declared_dependency_set(self):
        root = self.make_project()
        result = _cli(SCRIPTS / "gg.py", "doctor", root)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertIn("pypdf", value["dependencies"])
        self.assertIn("deps", value)
        rows = {row["import_name"]: row for row in value["deps"]["dependencies"]}
        self.assertEqual({"docx", "openpyxl", "pypdf"}, set(rows))
        self.assertEqual(value["deps"]["selected_python"],
                         value["deps"]["current_python"])

    def test_doctor_falls_back_and_says_so(self):
        root = self.make_project()
        gg = runtime("gg")
        mod = runtime("gg_deps")
        buf = io.StringIO()
        with mock.patch.object(mod, "doctor",
                               side_effect=RuntimeError("probe broken")), \
                contextlib.redirect_stdout(buf):
            code = gg.main(["doctor", str(root)])
        self.assertEqual(0, code)
        value = json.loads(buf.getvalue())
        self.assertEqual("unavailable", value["deps"]["status"])
        self.assertIn("현재 CLI 인터프리터 기준", value["notice"])
        self.assertIn("pypdf", value["dependencies"])


class BuildDocxTests(ContractCase):
    """JUDGE-A #2/#3 and JUDGE-B #8/#12/#13."""

    def _project(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        return root

    def test_an_empty_source_is_not_a_docx(self):
        root = self._project()
        (root / "build").mkdir(exist_ok=True)
        (root / "build" / "empty.md").write_text("  \n", encoding="utf-8")
        result = _cli(SCRIPTS / "build_docx.py", root,
                      "--major", "specialty_crops",
                      "--in", "build/empty.md", "--out", "build/out.docx")
        self.assertEqual(2, result.returncode, result.stdout)
        value = json.loads(result.stdout)
        self.assertEqual("blocked", value["status"])
        self.assertIn("본문", value["reason"])
        self.assertFalse((root / "build" / "out.docx").exists())

    def test_a_failure_reports_in_the_envelope_shape(self):
        root = self._project()
        result = _cli(SCRIPTS / "build_docx.py", root,
                      "--major", "specialty_crops",
                      "--in", "build/없는파일.md", "--out", "build/out.docx")
        self.assertEqual(2, result.returncode, result.stdout)
        value = json.loads(result.stdout)
        self.assertEqual("blocked", value["status"])
        self.assertIn("없는파일", value["reason"])
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_docx_dependency_is_a_blocked_envelope(self):
        root = self._project()
        script = (
            "import sys, json\n"
            "sys.modules['docx'] = None\n"
            "sys.path.insert(0, %r)\n"
            "import build_docx\n"
            "code = build_docx.main([%r, '--major', 'specialty_crops',"
            " '--in', 'build/x.md', '--out', 'build/x.docx'])\n"
            "sys.exit(code)\n" % (str(SCRIPTS), str(root))
        )
        result = _cli("-c", script)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual("blocked", value["status"])
        self.assertIn("의존성 미설치", value["reason"])

    def test_paper_then_docx_defaults_line_up(self):
        root = self._project()
        spec = root / "paper-spec.json"
        spec.write_text(json.dumps(
            {"author": "합성", "writing_year": 2026, "years": 5,
             "school_profile": {"mode": "school", "school": "합성대학교",
                                "department": "특용작물학과"}},
            ensure_ascii=False), encoding="utf-8")
        paper = _cli(SCRIPTS / "gg.py", "paper", root,
                     "--input", "paper-spec.json",
                     "--major", "specialty_crops")
        self.assertEqual(0, paper.returncode, paper.stdout + paper.stderr)
        docx = _cli(SCRIPTS / "build_docx.py", root,
                    "--major", "specialty_crops")
        self.assertEqual(0, docx.returncode, docx.stdout + docx.stderr)
        self.assertTrue((root / "build" / "검토전_논문.docx").is_file())

    def test_sub_heading_sizes_pass_the_school_format_probe(self):
        build = runtime("build_docx")
        self.assertEqual({6: 13, 7: 13, 8: 13, 9: 13},
                         {k: build.LEVEL_PT[k] for k in (6, 7, 8, 9)})
