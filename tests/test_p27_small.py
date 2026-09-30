"""P27 small-item lane (W3b): K3-03 school_profile.major_id,
K3-02 per-cause held guidance, K2-03 pywin32 removal, K4-06
official-toc runtime_present truth, K2-02 UTF-8 stdio contract."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tests._harness import (
    SCRIPTS, ContractCase, bind_major, claim_of, fact_op, runtime,
    source_op, write_text,
)

REFERENCES = SCRIPTS.parent / "references"


def _cli(script, *args, env=None):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, env=env)


def _bind_major_version(root, major_id, module_version):
    """Like _harness.bind_major but pins an arbitrary module_version —
    needed to record a stale 0.1.0 hort binding (K3-02)."""
    core = runtime("gg_core")
    write_text(root, "major-answer.txt", "전공 선택: " + major_id + "\n")
    fact = fact_op("selected_major", "common.major_id", major_id, "",
                   scope="project", verification="claim_supported",
                   source_id="major-answer", claim_id="major")
    fact["value"]["module_version"] = module_version
    source = source_op("major-answer.txt",
                       claims=claim_of(fact["value"], "major"))
    source["value"]["id"] = "major-answer"
    revision = core.load(root)["revision"]
    core.apply(root, {"request_id": "bind-%s-%s" % (major_id, module_version),
                      "ops": [source, fact]}, revision)


class K303SchoolProfileMajorId(ContractCase):
    """K3-03: school_profile.major_id is a supported nested key."""

    def _spec(self, **profile_extra):
        spec = {
            "major": "특용작물전공",
            "crop": "도라지",
            "title": "도라지 신규 창업 — 합성 시험값",
            "writing_year": 2026,
            "business_form": "신규 창업",
            "region": "경북 봉화",
            "school_profile": {
                "mode": "school",
                "layout": "forms_1_to_4",
                "title": "도라지 신규 창업 — 합성 시험값",
                "department": "특용작물전공",
            },
        }
        spec["school_profile"].update(profile_extra)
        return spec

    def test_nested_major_id_only_builds_paper(self):
        """Nested-only school_profile.major_id reaches the paper skeleton
        (diagnosis input paper-profile-only.json, reconstructed on a fresh
        init project)."""
        root = self.make_project()
        bind_major(root, "specialty_crops")
        spec = self._spec(major_id="specialty_crops")
        write_text(root, "inputs/paper-profile-only.json",
                   json.dumps(spec, ensure_ascii=False))
        proc = _cli("gg_school_paper.py", root,
                    "--input", "inputs/paper-profile-only.json",
                    "--out", "build/profile-only.md")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = (root / "build" / "profile-only.md").read_text(
            encoding="utf-8")
        self.assertIn("겉표지", text)
        self.assertIn("인준서", text)
        self.assertIn("목차", text)

    def test_nested_major_id_is_normalized(self):
        fm = runtime("gg_frontmatter")
        profile = fm.normalize_school_profile(
            {"school_profile": {"mode": "school",
                                "major_id": "specialty_crops"}})
        self.assertTrue(profile["enabled"])
        self.assertEqual(profile["major_id"], "specialty_crops")

    def test_conflicting_major_ids_rejected(self):
        fm = runtime("gg_frontmatter")
        with self.assertRaises(ValueError) as caught:
            fm.normalize_school_profile(
                {"major_id": "specialty_crops",
                 "school_profile": {"mode": "school",
                                    "major_id": "industrial_insects"}})
        self.assertEqual(str(caught.exception),
                         "school_profile.major_id와 major_id 불일치")

    def test_agreeing_major_ids_accepted(self):
        fm = runtime("gg_frontmatter")
        profile = fm.normalize_school_profile(
            {"major_id": "specialty_crops",
             "school_profile": {"mode": "school",
                                "major_id": "specialty_crops"}})
        self.assertEqual(profile["major_id"], "specialty_crops")


class K302HeldGuidance(ContractCase):
    """K3-02: held guidance is per-cause and a version-mismatch
    binding_invalid reports recorded/registered module versions."""

    def _hort_010_project(self):
        root = self.make_project()
        _bind_major_version(root, "hort_env_systems", "0.1.0")
        return root

    def test_major_plan_held_reports_both_versions(self):
        """gg.py major-plan on a 0.1.0 hort binding -> held
        binding_invalid with recorded/registered version fields and the
        version-mismatch guidance sentence."""
        root = self._hort_010_project()
        proc = _cli("gg.py", "major-plan", root)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        value = json.loads(proc.stdout)
        self.assertEqual(value["status"], "held")
        self.assertEqual(value["reason"], "binding_invalid")
        self.assertEqual(value["recorded_version"], "0.1.0")
        self.assertEqual(value["registered_version"], "0.2.0")
        self.assertIn(
            "기록된 모듈 버전 0.1.0이 등록 버전 0.2.0와 다릅니다",
            value["guidance"])
        self.assertIn("새 revision으로 0.2.0를 다시 기록하세요",
                      value["guidance"])

    def test_authorize_binding_invalid_carries_versions(self):
        """authorize_output wraps the same cause as major_binding_invalid
        with the two version fields and the version guidance."""
        mc = runtime("gg_major_contract")
        root = self._hort_010_project()
        with self.assertRaises(mc.OutputHeldError) as caught:
            mc.authorize_output(
                mc.OUTPUT_SCHOOL_PAPER,
                mc.output_context(root, "hort_env_systems"))
        detail = caught.exception.detail
        self.assertEqual(caught.exception.reason, "major_binding_invalid")
        self.assertEqual(detail["recorded_version"], "0.1.0")
        self.assertEqual(detail["registered_version"], "0.2.0")
        self.assertIn("등록 버전 0.2.0", detail["guidance"])

    def test_unsupported_output_guidance(self):
        mc = runtime("gg_major_contract")
        root = self.make_project()
        bind_major(root, "fruit_trees")
        with self.assertRaises(mc.OutputHeldError) as caught:
            mc.authorize_output(
                mc.OUTPUT_SCHOOL_PAPER,
                mc.output_context(root, "fruit_trees"))
        self.assertEqual(caught.exception.reason, "unsupported_output")
        self.assertEqual(caught.exception.detail["guidance"],
                         mc.OUTPUT_GUIDANCE_UNSUPPORTED)

    def test_missing_major_keeps_display_guidance(self):
        mc = runtime("gg_major_contract")
        root = self.make_project()
        with self.assertRaises(mc.OutputHeldError) as caught:
            mc.authorize_output(
                mc.OUTPUT_SCHOOL_PAPER,
                mc.output_context(root, "specialty_crops"))
        self.assertEqual(caught.exception.reason, "major_binding_required")
        self.assertEqual(caught.exception.detail["guidance"],
                         mc.OUTPUT_GUIDANCE)

    def test_common_only_plan_from_reason_string(self):
        mc = runtime("gg_major_contract")
        plan = mc.common_only_plan("major_required")
        self.assertEqual(plan["status"], "held")
        self.assertEqual(plan["reason"], "major_required")
        self.assertEqual(plan["guidance"], mc.OUTPUT_GUIDANCE)


class K203RuntimeDeps(ContractCase):
    """K2-03: pywin32 is out of the runtime dependency manifest."""

    def test_pywin32_removed_and_doctor_runs(self):
        manifest = json.loads(
            (SCRIPTS / "runtime-deps.json").read_text(encoding="utf-8"))
        self.assertNotIn(
            "pywin32", {d["dist"] for d in manifest["dependencies"]})
        self.assertNotIn(
            "pywin32",
            (SCRIPTS / "requirements-runtime.txt").read_text(
                encoding="utf-8"))
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-p27-deps-")
        self.addCleanup(tmp.cleanup)
        proc = _cli("gg_deps.py", "doctor", tmp.name)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(
            {r["dist"] for r in report["dependencies"]},
            {"openpyxl", "python-docx", "pypdf"})
        for row in report["dependencies"]:
            self.assertIn(
                row["status"],
                {"installed", "missing", "unsupported-version",
                 "installed-version-unchecked"})


class K406BuiltinSources(ContractCase):
    """K4-06: the two official-toc.md registry entries declare
    runtime_present=false (the file is not shipped)."""

    def test_official_toc_entries_not_runtime_present(self):
        doc = json.loads((REFERENCES / "builtin-sources.json").read_text(
            encoding="utf-8"))
        targets = {"official-writing-guide-pdf",
                   "official-writing-guide-hwp"}
        found = {e["source_id"]: e for e in doc["entries"]
                 if e.get("source_id") in targets}
        self.assertEqual(set(found), targets)
        for sid, entry in found.items():
            self.assertIs(entry["runtime_present"], False, sid)

    def test_registry_still_validates(self):
        registry = runtime("gg_reuse").load_registry(
            REFERENCES / "builtin-sources.json")
        entries = {e.get("source_id"): e
                   for e in registry.document["entries"]}
        for sid in ("official-writing-guide-pdf",
                    "official-writing-guide-hwp"):
            self.assertIs(entries[sid]["runtime_present"], False, sid)


class K202Utf8Entry(ContractCase):
    """K2-02: representative CLIs run --help with a CP949 stdio band and
    no PYTHONIOENCODING override — exit 0, no UnicodeEncodeError."""

    CLIS = ("build_docx.py", "gg_excel_template.py", "gg_school_paper.py",
            "gg_deps.py", "gg_office.py")

    # 자식 Python에서 stdio를 명시적으로 CP949로 내려 실제 대역을
    # probe한 뒤 runpy로 정상 CLI를 실행한다 — LC_ALL 같은 호스트
    # locale 설치 여부에 의존하지 않는다.
    _CP949_PROBE = (
        "import sys\n"
        "sys.stdout.reconfigure(encoding='cp949')\n"
        "sys.stderr.reconfigure(encoding='cp949')\n"
        "print(sys.stdout.encoding, sys.stderr.encoding)\n")
    _CP949_RUN = (
        "import os, runpy, sys\n"
        "sys.stdout.reconfigure(encoding='cp949')\n"
        "sys.stderr.reconfigure(encoding='cp949')\n"
        "print(sys.stdout.encoding, sys.stderr.encoding, file=sys.stderr)\n"
        "del sys.argv[0]\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n")

    def _cp949_env(self):
        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def test_help_under_cp949_locale(self):
        env = self._cp949_env()
        probe = subprocess.run(
            [sys.executable, "-B", "-c", self._CP949_PROBE],
            capture_output=True, text=True, env=env)
        # The reconfigure really drops the pipe band to cp949 —
        # otherwise this leg proves nothing and must not silently pass.
        self.assertEqual(probe.stdout.strip(), "cp949 cp949",
                         probe.stderr)
        for script in self.CLIS:
            with self.subTest(script=script):
                proc = subprocess.run(
                    [sys.executable, "-B", "-c", self._CP949_RUN,
                     str(SCRIPTS / script), "--help"],
                    capture_output=True, text=True, env=env)
                self.assertIn("cp949 cp949", proc.stderr)
                self.assertEqual(
                    proc.returncode, 0,
                    proc.stdout[-500:] + proc.stderr[-500:])
                self.assertNotIn("UnicodeEncodeError", proc.stderr)
                self.assertNotIn("codec can't encode", proc.stderr)


if __name__ == "__main__":
    import unittest
    unittest.main()
