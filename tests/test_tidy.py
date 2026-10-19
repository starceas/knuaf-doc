"""Output-hygiene contract: tidy preview/apply over ``.gg-work`` runs."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from tests._harness import ContractCase, runtime


class TidyTests(ContractCase):
    def setUp(self):
        self.tidy = runtime("gg_tidy")
        self.lock = runtime("gg_lock")
        self.core = runtime("gg_core")

    def _run(self, root, artifact, run, files=None):
        path = Path(root) / ".gg-work" / artifact / run
        path.mkdir(parents=True)
        for name, text in (files or {"trial.txt": "trial"}).items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, bytes):
                target.write_bytes(text)
            else:
                target.write_text(text, encoding="utf-8")
        return path

    def _paths(self, entries):
        return {entry["path"] for entry in entries}

    def _reasons(self, result):
        return {
            entry["path"]: entry["reason"]
            for entry in result["kept"]
            if entry.get("reason")
        }

    def test_preview_reports_without_changing_the_tree(self):
        root = self.make_project()
        self._run(root, "finance", "r001")
        self._run(root, "finance", "r002", {"check.json": "{}"})
        (Path(root) / "build-f2").mkdir()
        (Path(root) / "build-f2" / "old.xlsx").write_text("old")
        (Path(root) / "재무_F2.xlsx").write_text("old")

        before = self._tree_bytes(root)
        result = self.tidy.tidy(root)

        self.assertEqual("preview", result["status"])
        self.assertFalse(result["applied"])
        self.assertEqual(
            {".gg-work/finance/r001"}, self._paths(result["candidates"])
        )
        self.assertEqual(
            {"build-f2", "재무_F2.xlsx"},
            self._paths(result["outside_candidates"]),
        )
        self.assertEqual("latest", self._reasons(result)[".gg-work/finance/r002"])
        self.assertEqual(before, self._tree_bytes(root))

    def test_apply_deletes_only_plain_old_runs(self):
        root = self.make_project()
        self._run(root, "finance", "r001")
        self._run(root, "finance", "r002", {".keep": ""})
        self._run(root, "finance", "r003")
        (Path(root) / ".gg-work" / "finance" / "scratch").mkdir()
        (Path(root) / ".gg-work" / "finance" / "scratch" / "note.txt").write_text("x")

        self._run(root, "paper", "r001")
        self._run(root, "paper", "r002")
        project = json.loads((Path(root) / "project.json").read_text(encoding="utf-8"))
        project["output_hygiene_ref"] = ".gg-work/paper/r001/draft.json"
        (Path(root) / "project.json").write_text(
            json.dumps(project, ensure_ascii=False), encoding="utf-8"
        )

        self._run(root, "poem", "r001")
        self._run(root, "poem", "r002", {
            "receipt.json": '{"path": ".gg-work/poem/r001/native.pdf"}',
        })

        self._run(root, "novel", "r001", {".keep": ""})
        self._run(root, "novel", "r002")

        self._run(root, "essay", "r001", {
            "self.json": '{"self": ".gg-work/essay/r001/self.json"}',
        })
        self._run(root, "essay", "r002")

        notes = Path(root) / ".gg-work" / "notes"
        notes.mkdir(parents=True)
        (notes / "ORDER.md").write_text("order")
        outside = Path(root) / "build-f2"
        outside.mkdir()
        (outside / "old.xlsx").write_text("old")

        result = self.tidy.tidy(root, apply=True)

        self.assertEqual("applied", result["status"])
        self.assertEqual(
            {".gg-work/essay/r001", ".gg-work/finance/r001"},
            self._paths(result["deleted"]),
        )
        self.assertFalse((Path(root) / ".gg-work/finance/r001").exists())
        self.assertFalse((Path(root) / ".gg-work/essay/r001").exists())
        self.assertTrue((Path(root) / ".gg-work/finance/r002/.keep").is_file())
        self.assertTrue((Path(root) / ".gg-work/finance/r003/trial.txt").is_file())
        self.assertTrue((Path(root) / ".gg-work/paper/r001/trial.txt").is_file())
        self.assertTrue((Path(root) / ".gg-work/poem/r001/trial.txt").is_file())
        self.assertTrue((Path(root) / ".gg-work/novel/r001/.keep").is_file())
        self.assertTrue((Path(root) / ".gg-work/notes/ORDER.md").is_file())
        self.assertTrue((Path(root) / ".gg-work/finance/scratch/note.txt").is_file())
        self.assertTrue((outside / "old.xlsx").is_file())

        reasons = self._reasons(result)
        self.assertEqual("keep_file", reasons[".gg-work/finance/r002"])
        self.assertEqual("latest", reasons[".gg-work/finance/r003"])
        self.assertEqual("referenced", reasons[".gg-work/paper/r001"])
        self.assertEqual("referenced", reasons[".gg-work/poem/r001"])
        self.assertEqual("keep_file", reasons[".gg-work/novel/r001"])

        self.assertEqual("free", self.lock.probe_lock(root)["lock_state"])
        with self.lock.Lock(root):
            pass

    def test_apply_releases_lock_and_import_still_copies_gg_work(self):
        root = self.make_project()
        self._run(root, "finance", "r001")
        self._run(root, "finance", "r002")
        self.assertEqual("applied", self.tidy.tidy(root, apply=True)["status"])

        with tempfile.TemporaryDirectory(prefix="tidy-import-") as td:
            base = Path(td)
            source = base / "legacy"
            source.mkdir()
            (source / "answer.txt").write_text("answer")
            run = source / ".gg-work" / "finance" / "r001"
            run.mkdir(parents=True)
            (run / "trial.txt").write_text("trial")
            self.core.migrate(source, base / "dest", offline_confirmed=True)
            self.assertTrue(
                (base / "dest/.gg-work/finance/r001/trial.txt").is_file()
            )

    def test_symlink_runs_are_preserved(self):
        root = self.make_project()
        with tempfile.TemporaryDirectory(prefix="tidy-link-") as td:
            outside = Path(td) / "outside"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep")
            (Path(root) / ".gg-work/finance").mkdir(parents=True)
            try:
                os.symlink(outside, Path(root) / ".gg-work/finance/r001")
            except OSError:
                self.skipTest("symlink unsupported")
            self._run(root, "finance", "r002")
            result = self.tidy.tidy(root, apply=True)
            self.assertEqual("applied", result["status"])
            self.assertTrue(os.path.islink(Path(root) / ".gg-work/finance/r001"))
            self.assertTrue((outside / "keep.txt").is_file())
            self.assertEqual(
                "symlink",
                self._reasons(result)[".gg-work/finance/r001"],
            )

    def test_nested_symlink_preserves_the_run(self):
        root = self.make_project()
        with tempfile.TemporaryDirectory(prefix="tidy-link-") as td:
            outside = Path(td) / "outside.txt"
            outside.write_text("keep")
            run = self._run(root, "finance", "r001")
            try:
                os.symlink(outside, run / "link.txt")
            except OSError:
                self.skipTest("symlink unsupported")
            self._run(root, "finance", "r002")
            result = self.tidy.tidy(root, apply=True)
            self.assertEqual("applied", result["status"])
            self.assertTrue((run / "link.txt").is_symlink())
            self.assertTrue((run / "link.txt").is_file())
            self.assertEqual(
                "symlink", self._reasons(result)[".gg-work/finance/r001"]
            )

    def test_unreadable_json_mention_preserves_the_run(self):
        root = self.make_project()
        self._run(root, "finance", "r001")
        self._run(root, "finance", "r002")
        build = Path(root) / "build"
        build.mkdir()
        (build / "receipt.json").write_bytes(
            b"\xff\xfe.gg-work/finance/r001/check.json"
        )
        result = self.tidy.tidy(root, apply=True)
        self.assertEqual("applied", result["status"])
        self.assertTrue((Path(root) / ".gg-work/finance/r001/trial.txt").is_file())
        self.assertIn(
            self._reasons(result)[".gg-work/finance/r001"],
            {"referenced", "unreadable_reference"},
        )

    def test_apply_without_lock_protocol_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="tidy-nolock-") as td:
            root = Path(td) / "workspace"
            root.mkdir()
            self._run(root, "finance", "r001")
            self._run(root, "finance", "r002")
            before = self._tree_bytes(root)
            result = self.tidy.tidy(root, apply=True)
            self.assertEqual("blocked", result["status"])
            self.assertFalse(result["applied"])
            self.assertEqual(before, self._tree_bytes(root))
            self.assertIn(result["lock_state"], {"absent", "initializing"})

    def test_apply_is_refused_while_another_writer_holds_the_lock(self):
        root = self.make_project()
        self._run(root, "finance", "r001")
        self._run(root, "finance", "r002")
        with self.lock.Lock(root):
            result = self.tidy.tidy(root, apply=True)
        self.assertEqual("blocked", result["status"])
        self.assertEqual("busy", result["lock_state"])
        self.assertTrue((Path(root) / ".gg-work/finance/r001/trial.txt").is_file())

        after = self.tidy.tidy(root, apply=True)
        self.assertEqual("applied", after["status"])
        self.assertFalse((Path(root) / ".gg-work/finance/r001").exists())

    def test_run_numbers_sort_numerically(self):
        root = self.make_project()
        self._run(root, "finance", "r2")
        self._run(root, "finance", "r10")
        self._run(root, "finance", "r9")
        result = self.tidy.tidy(root)
        self.assertEqual(
            {".gg-work/finance/r2", ".gg-work/finance/r9"},
            self._paths(result["candidates"]),
        )
        self.assertEqual(
            "latest", self._reasons(result)[".gg-work/finance/r10"]
        )

    def test_reference_prefix_does_not_protect_a_shorter_name(self):
        root = self.make_project()
        self._run(root, "finance", "r001")
        self._run(root, "finance", "r0010")
        project = json.loads(
            (Path(root) / "project.json").read_text(encoding="utf-8")
        )
        project["ref"] = ".gg-work/finance/r0010/check.json"
        (Path(root) / "project.json").write_text(
            json.dumps(project, ensure_ascii=False), encoding="utf-8"
        )
        result = self.tidy.tidy(root)
        self.assertEqual(
            {".gg-work/finance/r001"}, self._paths(result["candidates"])
        )

    def test_symlinked_artifact_directory_is_preserved(self):
        root = self.make_project()
        with tempfile.TemporaryDirectory(prefix="tidy-art-") as td:
            outside = Path(td) / "finance"
            (outside / "r001").mkdir(parents=True)
            (outside / "r001" / "trial.txt").write_text("outside")
            (outside / "r002").mkdir()
            (Path(root) / ".gg-work").mkdir()
            try:
                os.symlink(outside, Path(root) / ".gg-work/finance")
            except OSError:
                self.skipTest("symlink unsupported")
            result = self.tidy.tidy(root, apply=True)
            self.assertEqual("applied", result["status"])
            self.assertEqual([], result["deleted"])
            self.assertEqual(
                "symlink_artifact",
                self._reasons(result)[".gg-work/finance"],
            )
            self.assertTrue((outside / "r001/trial.txt").is_file())

    def test_apply_without_work_dir_is_a_noop(self):
        root = self.make_project()
        before = self._tree_bytes(root)
        result = self.tidy.tidy(root, apply=True)
        self.assertEqual("applied", result["status"])
        self.assertEqual([], result["deleted"])
        self.assertEqual("not_needed", result["lock"])
        self.assertEqual(before, self._tree_bytes(root))

    def test_json_escaped_references_are_preserved(self):
        root = self.make_project()
        run = self._run(root, "재무", "r001")
        self._run(root, "재무", "r002")
        build = Path(root) / "build"
        build.mkdir()
        cases = {
            "unicode": json.dumps(
                {"evidence_path": ".gg-work/재무/r001/evidence.txt"},
                ensure_ascii=True,
            ),
            "backslash": json.dumps(
                {"evidence_path": ".gg-work\\재무\\r001\\evidence.txt"}
            ),
            "escaped_slash": (
                '{"evidence_path": ".gg-work\\/재무\\/r001\\/evidence.txt"}'
            ),
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                (build / "receipt.json").write_text(text, encoding="utf-8")
                result = self.tidy.tidy(root, apply=True)
                self.assertEqual("applied", result["status"])
                self.assertEqual([], result["deleted"])
                self.assertTrue((run / "trial.txt").is_file())
                self.assertEqual(
                    "referenced",
                    self._reasons(result)[".gg-work/재무/r001"],
                )

        (build / "receipt.json").write_text(
            cases["unicode"], encoding="utf-16"
        )
        result = self.tidy.tidy(root, apply=True)
        self.assertEqual([], result["deleted"])
        self.assertTrue((run / "trial.txt").is_file())

    def test_json_keys_are_searched_too(self):
        root = self.make_project()
        run = self._run(root, "finance", "r001")
        self._run(root, "finance", "r002")
        build = Path(root) / "build"
        build.mkdir()
        (build / "receipt.json").write_text(
            json.dumps({".gg-work/finance/r001/key.json": "value"}),
            encoding="utf-8",
        )
        result = self.tidy.tidy(root, apply=True)
        self.assertEqual([], result["deleted"])
        self.assertTrue((run / "trial.txt").is_file())

    def test_unreadable_reference_area_blocks_apply(self):
        root = self.make_project()
        run = self._run(root, "finance", "r001")
        self._run(root, "finance", "r002")
        build = Path(root) / "build"
        build.mkdir()
        (build / "receipt.json").write_text(
            json.dumps(
                {"evidence": ".gg-work/finance/r001/evidence.txt"}
            ),
            encoding="utf-8",
        )
        build.chmod(0)
        try:
            try:
                list(build.iterdir())
            except PermissionError:
                pass
            else:
                self.skipTest("permission failure not reproducible")

            preview = self.tidy.tidy(root)
            self.assertTrue(preview["scan_errors"])
            applied = self.tidy.tidy(root, apply=True)
            self.assertEqual("blocked", applied["status"])
            self.assertEqual("reference_scan_incomplete", applied["reason"])
            self.assertFalse(applied["applied"])
            self.assertTrue((run / "trial.txt").is_file())
        finally:
            build.chmod(0o700)

    def test_names_outside_the_run_rule_are_preserved(self):
        root = self.make_project()
        odd = Path(root) / ".gg-work" / "finance" / "r001\n"
        odd.mkdir(parents=True)
        (odd / "evidence.txt").write_text("must preserve")
        self._run(root, "finance", "r002")
        preview = self.tidy.tidy(root)
        self.assertEqual([], preview["candidates"])
        self.assertEqual(
            "non_run", self._reasons(preview)[".gg-work/finance/r001\n"]
        )
        result = self.tidy.tidy(root, apply=True)
        self.assertEqual("applied", result["status"])
        self.assertEqual([], result["deleted"])
        self.assertTrue((odd / "evidence.txt").is_file())

    def test_damaged_json_with_raw_backslash_path_is_preserved(self):
        root = self.make_project()
        run = self._run(root, "finance", "r001")
        self._run(root, "finance", "r002")
        receipt = Path(root) / "receipt.json"
        cases = {
            "slash": b"\xff.gg-work/finance/r001/evidence.txt",
            "backslash": b"\xff.gg-work\\finance\\r001\\evidence.txt",
            "utf16_backslash": (
                b"\xff\xfe"
                + ".gg-work\\finance\\r001\\evidence.txt".encode("utf-16-le")
            ),
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                receipt.write_bytes(data)
                preview = self.tidy.tidy(root)
                self.assertEqual([], preview["candidates"])
                result = self.tidy.tidy(root, apply=True)
                self.assertEqual([], result["deleted"])
                self.assertTrue((run / "trial.txt").is_file())
                self.assertEqual(
                    "referenced",
                    self._reasons(preview)[".gg-work/finance/r001"],
                )

    def test_outside_candidates_are_reported_at_any_depth(self):
        root = self.make_project()
        finance = Path(root) / "finance"
        deep = finance / "build-f2"
        deep.mkdir(parents=True)
        (deep / "build-f9").mkdir()
        (deep / "old.xlsx").write_text("old")
        (finance / "재무_F2.xlsx").write_text("old")
        (finance / ".gg-work" / "build-f7").mkdir(parents=True)
        (finance / ".git" / "build-f8").mkdir(parents=True)
        (finance / "run3").mkdir()
        with tempfile.TemporaryDirectory(prefix="tidy-outside-") as td:
            target = Path(td) / "elsewhere"
            target.mkdir()
            try:
                os.symlink(target, finance / "build-f5")
            except OSError:
                self.skipTest("symlink unsupported")

            result = self.tidy.tidy(root)

            self.assertEqual(
                {
                    "finance/build-f2",
                    "finance/run3",
                    "finance/재무_F2.xlsx",
                    "finance/build-f5",
                },
                self._paths(result["outside_candidates"]),
            )
            kinds = {
                item["path"]: item["kind"]
                for item in result["outside_candidates"]
            }
            self.assertEqual("dir", kinds["finance/build-f2"])
            self.assertEqual("file", kinds["finance/재무_F2.xlsx"])
            self.assertEqual("link", kinds["finance/build-f5"])
            self.assertEqual([], result["outside_scan_errors"])

            applied = self.tidy.tidy(root, apply=True)
            self.assertEqual([], applied["deleted"])
            self.assertTrue((deep / "old.xlsx").is_file())
            self.assertTrue((finance / "재무_F2.xlsx").is_file())

    def test_cli_preview_then_apply(self):
        root = self.make_project()
        self._run(root, "finance", "r001")
        self._run(root, "finance", "r002")
        gg = runtime("gg")

        preview = io.StringIO()
        with contextlib.redirect_stdout(preview):
            code = gg.main(["tidy", str(root)])
        self.assertEqual(0, code)
        self.assertEqual("preview", json.loads(preview.getvalue())["status"])

        applied = io.StringIO()
        with contextlib.redirect_stdout(applied):
            code = gg.main(["tidy", str(root), "--apply"])
        self.assertEqual(0, code)
        value = json.loads(applied.getvalue())
        self.assertEqual("applied", value["status"])
        self.assertEqual(
            [".gg-work/finance/r001"],
            [entry["path"] for entry in value["deleted"]],
        )
        self.assertFalse((Path(root) / ".gg-work/finance/r001").exists())


if __name__ == "__main__":
    unittest.main()
