"""Documented-command contract tests (DESIGN §5).

Each case runs the command exactly as the docs describe it and asserts
on the command *result*, never on documentation prose.  Two cases are
pinned to the post-W3b contract shape and are expected to fail until
the W3b lane lands K3-02/K3-03:

- test_school_profile_major_id          (K3-03 frontmatter major_id)
- test_hort_env_020_binding_major_plan  (K3-02 binding_invalid versions)
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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


def _env():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "gg.py"), *args],
        capture_output=True, text=True, env=_env(), cwd=cwd)


def _seed(core, root):
    """Minimal project: one source+fact+section triple at revision 1."""
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
    return core.apply(root, {"request_id": "seed", "ops": ops}, 0)


def _output_value(core, root, path, fmt, data):
    p = core.load(root)
    refs = core.sort_target_refs(core._export_refs(p))
    return {
        "id": Path(path).stem,
        "path": path,
        "format": fmt,
        "file_hash": core.digest(data),
        "target_refs": refs,
        "input_fingerprint": core.fingerprint(root, p, refs),
    }


class DocumentedCommandContractTests(ContractCase):
    """DESIGN §5: the commands as written in the docs must run and
    produce the documented result shape."""

    def test_import_offline_confirmed_documented_command(self):
        """`gg.py import <src> --out <dest> --offline-confirmed` imports
        a legacy Markdown folder into a new guarded workspace."""
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-doc-contract-")
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "legacy-src"
        (src / "sections").mkdir(parents=True)
        (src / "sections" / "01-개요.md").write_text(
            "# 개요\n본문\n", encoding="utf-8")
        dest = Path(tmp.name) / "imported"
        proc = _cli(
            "import", str(src), "--out", str(dest), "--offline-confirmed")
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertTrue((dest / "project.json").is_file())

    def test_doctor_probe_is_documented_read_only(self):
        """`gg.py doctor <folder> --probe` reports lock state without
        writing anything."""
        root = self.make_project()
        before = {str(p.relative_to(root)): p.read_bytes()
                  for p in sorted(root.rglob("*")) if p.is_file()}
        proc = _cli("doctor", str(root), "--probe")
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertIsInstance(payload, dict)
        after = {str(p.relative_to(root)): p.read_bytes()
                 for p in sorted(root.rglob("*")) if p.is_file()}
        self.assertEqual(before, after)

    def test_adopt_output_then_check_has_no_publication_error(self):
        """`gg.py adopt-output` followed by `gg.py check` reports no
        output_publication finding (K3-01 contract)."""
        core = runtime("gg_core")
        root = self.make_project()
        _seed(core, root)
        bind_major(root)
        (root / "out.md").write_bytes(b"OUT")
        ov = _output_value(core, root, "out.md", "md", b"OUT")
        spec = {"output": ov}
        (root / "adopt.json").write_text(
            json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        rev = core.load(root)["revision"]
        adopt = _cli(
            "adopt-output", str(root),
            "--input", "adopt.json",
            "--expected-revision", str(rev),
            "--request-id", "doc-contract-adopt",
            "--major", "specialty_crops")
        self.assertEqual(0, adopt.returncode,
                         adopt.stderr + adopt.stdout)
        check = _cli("check", str(root))
        # check exits 1 while any error-severity finding remains; the
        # contract is only that output_publication is not among them.
        rows = json.loads(check.stdout)
        bad = [
            r for r in rows
            if isinstance(r, dict)
            and r.get("check_id") == "output_publication"
            and r.get("status") != "pass"
        ]
        self.assertEqual([], bad)

    def test_school_profile_major_id(self):
        """DESIGN §5/K3-03 (W3b-pending): ``school_profile.major_id`` is
        an accepted key and must equal a top-level ``spec.major_id``
        when both are present."""
        fm = runtime("gg_frontmatter")
        profile = fm.normalize_school_profile({
            "title": "논문",
            "major_id": "hort_env_systems",
            "school_profile": {
                "major_id": "hort_env_systems",
                "author": "홍길동",
            },
        })
        self.assertEqual("hort_env_systems", profile.get("major_id"))
        with self.assertRaises(ValueError):
            fm.normalize_school_profile({
                "major_id": "specialty_crops",
                "school_profile": {"major_id": "hort_env_systems"},
            })

    def test_hort_env_020_binding_major_plan(self):
        """DESIGN §5: horticulture bound at module_version 0.2.0 answers
        `gg.py major-plan`; a stale recorded version is held as
        binding_invalid carrying recorded_version/registered_version
        (K3-02 shape — pending W3b)."""
        core = runtime("gg_core")
        root = self.make_project()
        _seed(core, root)
        bind_major(root, "hort_env_systems")
        proc = _cli("major-plan", str(root))
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual("hort_env_systems",
                         payload["binding"]["major_id"])
        self.assertEqual("0.2.0",
                         payload["binding"]["module_version"])

        # A binding recorded against a stale module_version is held, and
        # the held result names both versions for repair.
        p = core.load(root)
        fact = p["facts"]["selected_major"]
        fact["module_version"] = "9.9.9"
        (root / "project.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        held = _cli("major-plan", str(root))
        self.assertEqual(2, held.returncode, held.stderr + held.stdout)
        payload = json.loads(held.stdout)
        self.assertEqual("held", payload["status"])
        self.assertEqual("binding_invalid", payload["reason"])
        self.assertEqual("9.9.9", payload.get("recorded_version"))
        self.assertEqual("0.2.0", payload.get("registered_version"))


if __name__ == "__main__":
    unittest.main()
