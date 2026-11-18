"""Public apply/CLI school-outline contracts, using synthetic student answers."""
import copy
import json
import subprocess
import sys
from unittest.mock import patch

from tests._harness import ContractCase, SCRIPTS, fact_op, source_op, claim_of, write_text, runtime, section_op


class OutlineTests(ContractCase):
    def change(self, root, major="specialty_crops", request="bind", verification="claim_supported"):
        core = runtime("gg_core")
        module = runtime("gg_major_contract").default_registry().resolve(major)
        p = core.load(root)
        write_text(root, "major.txt", "전공 선택: " + major)
        fact = fact_op("major", "common.major_id", major, "", scope="project",
                       verification=verification, source_id="major", claim_id="major")
        fact["value"]["module_version"] = module.module_version
        fact["value"]["source_refs"][0]["revision"] = p["sources"].get("major", {}).get("revision", 0) + 1
        source = source_op("major.txt", claims=claim_of(fact["value"], "major"))
        source["value"]["id"] = "major"
        return {"request_id": request, "ops": [source, fact]}

    def test_all_school_profiles_and_request_replay(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        for major, count in [("specialty_crops", 6), ("dairy", 12), ("fruit_trees", 4), ("food_crops", 6)]:
            with self.subTest(major=major):
                root = self.make_project()
                change = self.change(root, major)
                original = copy.deepcopy(change)
                p = core.apply(root, change, 0)
                self.assertEqual(change, original)
                self.assertEqual((p["revision"], len(p["history"]), len(p["sections"])), (1, 1, count))
                for section in p["sections"].values():
                    self.assertEqual((section["status"], section["claims"], section["revision"]), ("empty", [], 1))
                    self.assertEqual(core.draft((root / section["path"]).read_text(encoding="utf-8")), "")
                    self.assertEqual(section["draft_hash"], core.digest(""))
                before = self._tree_bytes(root)
                self.assertEqual(outline.outline_state(str(root), p)["state"], "seeded")
                self.assertEqual(core.apply(root, change, 0)["revision"], 1)
                self.assertEqual(self._tree_bytes(root), before)
                self.assertEqual(p["requests"]["bind"]["hash"], core.digest(original))

    def test_cli_uses_same_commit(self):
        root = self.make_project()
        change = self.change(root)
        write_text(root, "change.json", json.dumps(change, ensure_ascii=False))
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "gg.py"), "apply", str(root),
                                 "--change", str(root / "change.json"), "--expected-revision", "0"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(runtime("gg_core").load(root)["sections"]), 6)

    def test_no_school_toc_and_invalid_binding(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        for major in ("industrial_insects", "hort_env_systems"):
            root = self.make_project()
            p = core.apply(root, self.change(root, major), 0)
            self.assertEqual(p["sections"], {})
            self.assertEqual(outline.outline_state(str(root), p)["state"], "no_school_toc")
        root = self.make_project()
        p = core.apply(root, self.change(root, verification="source_located"), 0)
        self.assertEqual(p["sections"], {})
        self.assertEqual(outline.outline_state(str(root), p)["state"], "not_bound")

    def test_existing_manuscript_preserved(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "sections/user.md", "학생 원고")
        p = core.apply(root, {"request_id":"body", "ops":[section_op("user", "내 장", "sections/user.md")]}, 0)
        record = copy.deepcopy(p["sections"])
        p = core.apply(root, self.change(root), 1)
        self.assertEqual(p["sections"], record)
        self.assertEqual((root / "sections/user.md").read_text(encoding="utf-8"), "학생 원고")
        self.assertEqual(runtime("gg_outline").outline_state(str(root), p)["state"], "existing_manuscript")

    def test_rebind_of_untouched_seeds_swaps_major_and_drops_old_files(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        root = self.make_project()
        core.apply(root, self.change(root), 0)
        p = core.apply(root, self.change(root, "dairy", "rebind"), 1)
        # 옛 빈 seed만 있으면 전공을 고칠 수 있다 — 새 seed가 같은 커밋에 선다.
        self.assertEqual(sorted(s["path"] for s in p["sections"].values()),
                         sorted(n["path"] for n in json.loads(
                             outline.CATALOG.read_text(encoding="utf-8")
                         )["profiles"]["dairy"]["sections"]))
        for rel in ("sections/farm_status.md", "sections/conclusion.md"):
            self.assertFalse((root / rel).exists())
        self.assertEqual(outline.outline_state(str(root), p)["state"], "seeded")

    def test_rebind_reuses_shared_paths_with_new_titles(self):
        core = runtime("gg_core")
        root = self.make_project()
        core.apply(root, self.change(root), 0)
        # 특용 → 식량: sections/conclusion.md·references.md 경로가 겹친다.
        before = (root / "sections/conclusion.md").read_text(encoding="utf-8")
        p = core.apply(root, self.change(root, "food_crops", "rebind"), 1)
        # 덮어쓰지 않고 새 장 제목으로 다시 쓴다(다시 손 안 댄 seed 상태).
        after = (root / "sections/conclusion.md").read_text(encoding="utf-8")
        self.assertNotEqual(after, before)
        self.assertEqual(after.splitlines()[0], "# " + p["sections"]["conclusion"]["title"])
        self.assertEqual(core.draft(after), "")
        self.assertEqual(p["sections"]["conclusion"]["draft_hash"], core.digest(""))
        # 겹치지 않는 옛 seed 파일은 걷힌다.
        self.assertFalse((root / "sections/farm_status.md").exists())
        self.assertFalse((root / "sections/environment.md").exists())

    def test_rebind_conflict_when_one_seed_has_writing(self):
        core = runtime("gg_core")
        root = self.make_project()
        p = core.apply(root, self.change(root), 0)
        section = p["sections"]["environment"]
        write_text(root, section["path"],
                   core.START + "\n학생이 쓴 본문\n" + core.END)
        before = (root / "project.json").read_bytes()
        seeds = {rel: data for rel, data in self._tree_bytes(root).items()
                 if rel.startswith("sections/")}
        with self.assertRaises(core.OperationError) as caught:
            core.apply(root, self.change(root, "dairy", "rebind"), 1)
        self.assertEqual(caught.exception.result["reason"], "outline_conflict")
        self.assertEqual((root / "project.json").read_bytes(), before)
        self.assertEqual({rel: data for rel, data in self._tree_bytes(root).items()
                          if rel.startswith("sections/")}, seeds)

    def test_rebind_conflict_when_receipt_does_not_match(self):
        core = runtime("gg_core")
        root = self.make_project()
        p = core.apply(root, self.change(root), 0)
        section = p["sections"]["environment"]
        # 본문은 여전히 비었지만 seed 때와 바이트가 다르다(제목만 바뀜).
        write_text(root, section["path"],
                   "# 다른 제목\n\n" + core.START + "\n" + core.END + "\n")
        before = (root / "project.json").read_bytes()
        with self.assertRaises(core.OperationError) as caught:
            core.apply(root, self.change(root, "dairy", "rebind"), 1)
        self.assertEqual(caught.exception.result["reason"], "outline_conflict")
        self.assertEqual((root / "project.json").read_bytes(), before)
        self.assertTrue((root / section["path"]).exists())

    def test_rebind_to_no_school_toc_removes_seeds(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        root = self.make_project()
        core.apply(root, self.change(root), 0)
        p = core.apply(root, self.change(root, "industrial_insects", "rebind"), 1)
        self.assertEqual(p["sections"], {})
        self.assertFalse((root / "sections/farm_status.md").exists())
        self.assertEqual(outline.outline_state(str(root), p)["state"], "no_school_toc")

    def test_rebind_keeps_student_sections(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        root = self.make_project()
        p = core.apply(root, self.change(root), 0)
        write_text(root, "sections/user.md", "학생 원고")
        p = core.apply(root, {"request_id": "body",
                              "ops": [section_op("user", "내 장", "sections/user.md")]}, 1)
        p = core.apply(root, self.change(root, "dairy", "rebind"), 2)
        # 학생이 쓴 절은 남고, 옛 빈 seed는 걷힌다. 원고가 있으면 새 목차를
        # 덧심지 않는다(학생 구조를 갈아치우지 않음) — existing_manuscript.
        self.assertEqual(p["sections"]["user"]["path"], "sections/user.md")
        self.assertEqual((root / "sections/user.md").read_text(encoding="utf-8"), "학생 원고")
        self.assertNotIn("farm_status", p["sections"])
        self.assertEqual(sorted(p["sections"]), ["user"])
        self.assertFalse((root / "sections/farm_status.md").exists())
        self.assertEqual(outline.outline_state(str(root), p)["state"],
                         "existing_manuscript")

    def test_rebind_failure_leaves_canonical_and_seed_files_untouched(self):
        core = runtime("gg_core")
        root = self.make_project()
        core.apply(root, self.change(root), 0)
        before = {rel: data for rel, data in self._tree_bytes(root).items()
                  if rel == "project.json" or rel.startswith("sections/")}
        change = self.change(root, "dairy", "rebind")
        with patch.object(core, "atomic", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(core.OperationError) as caught:
                core.apply(root, change, 1)
        result = caught.exception.result
        self.assertEqual(result["commit_state"], "not_committed")
        # 커밋 전 실패: 정본과 옛 seed 파일은 바이트 그대로다. 새 seed 파일은
        # W1과 같은 규칙으로 남기고 preserved_paths에 전부 보고한다(같은 요청
        # 재시도가 다시 쓰거나 그대로 재사용 — 조용히 지우지 않음).
        after = self._tree_bytes(root)
        for rel, data in before.items():
            self.assertEqual(after[rel], data)
        seeded = {rel for rel in after
                  if rel.startswith("sections/") and rel not in before}
        self.assertEqual(seeded, {rel for rel in result["preserved_paths"]
                                  if rel.startswith("sections/")})
        self.assertTrue(seeded)
        self.assertEqual(core.load(root)["revision"], 1)
        # 같은 요청 재시도는 남은 파일을 재사용해 그대로 성공한다.
        p = core.apply(root, change, 1)
        self.assertEqual(len(p["sections"]), 12)
        self.assertTrue((root / "sections/chapter_12.md").exists())
        self.assertFalse((root / "sections/farm_status.md").exists())
        self.assertEqual(p["requests"]["rebind"]["hash"], core.digest(change))

    def test_rebind_user_file_no_clobber(self):
        core = runtime("gg_core")
        root = self.make_project()
        change = self.change(root)
        write_text(root, "sections/farm_status.md", "学生 file")
        before = (root / "project.json").read_bytes()
        with self.assertRaises(core.OperationError):
            core.apply(root, change, 0)
        self.assertEqual((root / "project.json").read_bytes(), before)
        self.assertEqual((root / "sections/farm_status.md").read_text(encoding="utf-8"), "学生 file")

    def test_failure_preserves_owned_files_and_identical_retry(self):
        core = runtime("gg_core")
        root = self.make_project()
        change = self.change(root)
        before = (root / "project.json").read_bytes()
        with patch.object(core, "atomic", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(core.OperationError) as caught:
                core.apply(root, change, 0)
        self.assertEqual(caught.exception.result["commit_state"], "not_committed")
        self.assertEqual(len(caught.exception.result["preserved_paths"]), 7)
        self.assertEqual((root / "project.json").read_bytes(), before)
        p = core.apply(root, change, 0)
        self.assertEqual((p["revision"], len(p["sections"])), (1, 6))

    def test_catalog_matches_registry_body_nodes(self):
        outline, majors = runtime("gg_outline"), runtime("gg_major_contract")
        profiles = json.loads(outline.CATALOG.read_text(encoding="utf-8"))["profiles"]
        for major, profile in profiles.items():
            if profile["state"] == "no_school_toc":
                continue
            module = majors.default_registry().resolve(major)
            nodes = module.document_plan
            if major == "specialty_crops":
                expected = {n.section_id for n in nodes if n.role != "summary"}
            elif major == "fruit_trees":
                expected = {n.section_id for n in nodes if n.section_id.startswith(("ch1_", "ch2_", "ch3_", "ch4_"))}
            else:
                expected = {n.section_id for n in nodes if n.role in {"chapter", "conclusion", "references"}}
            self.assertEqual({n for s in profile["sections"] for n in s["registry_nodes"]}, expected)

    def test_complete_caller_outline_merges_but_partial_rejects(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        profile = json.loads(outline.CATALOG.read_text(encoding="utf-8"))["profiles"]["specialty_crops"]
        for complete in (False, True):
            root = self.make_project()
            change = self.change(root)
            nodes = profile["sections"] if complete else profile["sections"][:1]
            for node in nodes:
                write_text(root, node["path"], core.START + "\n" + core.END)
                change["ops"].append(section_op(node["node_id"], node["title"], node["path"],
                    node["order"], claims=[], status="empty"))
            if complete:
                p = core.apply(root, change, 0)
                self.assertEqual(outline.outline_state(str(root), p)["state"], "seeded")
            else:
                before = (root / "project.json").read_bytes()
                with self.assertRaises(core.OperationError):
                    core.apply(root, change, 0)
                self.assertEqual((root / "project.json").read_bytes(), before)

    def test_reads_and_unrelated_apply_do_not_seed_legacy(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        root = self.make_project()
        change = self.change(root)
        with patch.object(outline, "prepare", return_value=[]):
            p = core.apply(root, change, 0)
        before = self._tree_bytes(root)
        self.assertEqual(outline.outline_state(str(root), p)["reason"], "explicit_reconfirmation_required")
        self.assertEqual(self._tree_bytes(root), before)
        p = core.apply(root, {"request_id":"unrelated", "ops":[]}, 1)
        self.assertEqual(p["sections"], {})
        change = self.change(root, request="confirm")
        p = core.apply(root, change, 2)
        self.assertEqual(len(p["sections"]), 6)

    def test_final_candidate_independent_of_source_fact_order(self):
        core = runtime("gg_core")
        root = self.make_project()
        change = self.change(root)
        change["ops"].reverse()
        p = core.apply(root, change, 0)
        self.assertEqual(len(p["sections"]), 6)

    def test_body_updates_preserve_origin_and_reconfirmation_preserves_text(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        root = self.make_project()
        change = self.change(root)
        p = core.apply(root, change, 0)
        original = copy.deepcopy(p["sections"]["farm_status"])
        write_text(root, original["path"], core.START + "\n학생의 실제 초안\n" + core.END)
        op = section_op(original["id"], original["title"], original["path"], original["order"], claims=[])
        p = core.apply(root, {"request_id":"write", "ops":[op]}, 1)
        self.assertEqual(p["sections"][original["id"]]["outline"], original["outline"])
        record = copy.deepcopy(p["sections"])
        p = core.apply(root, self.change(root, request="confirm"), 2)
        self.assertEqual(p["sections"], record)
        self.assertEqual(core.draft((root / original["path"]).read_text(encoding="utf-8")), "학생의 실제 초안")
        self.assertEqual(outline.outline_state(str(root), p)["state"], "seeded")
        self.assertEqual(core.apply(root, change, 0)["revision"], 3)

    def test_empty_seed_does_not_start_progress(self):
        core = runtime("gg_core")
        root = self.make_project()
        core.apply(root, self.change(root), 0)
        payload = json.loads((root / ".knuaf-progress.json").read_text(encoding="utf-8"))
        self.assertEqual([s["state"] for s in payload["steps"]],
                         ["done", "now", "todo", "todo", "todo", "todo", "todo"])
        # 검사 행이 없어도 빈 seed 자체가 6단계의 본문 근거가 되면 안 된다.
        payload = runtime("gg_progress").assemble(core.load(root), [], {}, {})
        self.assertEqual(payload["steps"][5]["state"], "todo")

    def test_started_seed_counts_without_untouched_siblings(self):
        core, progress = runtime("gg_core"), runtime("gg_progress")
        for status in ("drafting", "review_ready"):
            with self.subTest(status=status):
                root = self.make_project()
                p = core.apply(root, self.change(root), 0)
                original = p["sections"]["farm_status"]
                write_text(root, original["path"], core.START + "\n학생의 실제 초안\n" + core.END)
                op = section_op(original["id"], original["title"], original["path"],
                                original["order"], claims=[], status=status)
                p = core.apply(root, {"request_id": "write", "ops": [op]}, 1)
                self.assertEqual(p["sections"][original["id"]]["status"], status)
                self.assertEqual(p["sections"][original["id"]]["outline"], original["outline"])
                written = json.loads((root / ".knuaf-progress.json").read_text(encoding="utf-8"))
                self.assertEqual(written["steps"][3],
                                 {"n": 4, "state": "done", "note": "본문 1개 절 초안 등록됨"})
                payload = progress.assemble(p, [], {}, {})
                self.assertEqual(payload["steps"][2]["state"], "done")
                self.assertEqual(payload["steps"][3],
                                 {"n": 4, "state": "done", "note": "본문 1개 절 초안 등록됨"})
                self.assertEqual(payload["steps"][5]["state"], "done")

    def test_legacy_empty_section_keeps_progress_and_denominator(self):
        core, progress = runtime("gg_core"), runtime("gg_progress")
        root = self.make_project()
        p = core.apply(root, self.change(root), 0)
        write_text(root, "legacy.md", core.START + "\n" + core.END)
        op = section_op("legacy", "기존 빈 장", "legacy.md", 99, claims=[], status="empty")
        p = core.apply(root, {"request_id": "legacy", "ops": [op]}, 1)
        self.assertNotIn("outline", p["sections"]["legacy"])
        payload = progress.assemble(p, [], {"state": "surplus"}, {})
        self.assertEqual(payload["steps"][2]["state"], "done")
        self.assertEqual(payload["steps"][3],
                         {"n": 4, "state": "now", "note": "0/1개 절 초안 씀"})
        self.assertEqual(payload["steps"][5]["state"], "done")

    def test_invalid_school_policy_rejects_valid_binding(self):
        core, outline = runtime("gg_core"), runtime("gg_outline")
        catalog = json.loads(outline.CATALOG.read_text(encoding="utf-8"))
        missing_profile = copy.deepcopy(catalog)
        del missing_profile["profiles"]["specialty_crops"]
        for content in ("{", json.dumps(missing_profile), None):
            with self.subTest(content=content):
                root = self.make_project()
                change = self.change(root)
                before = (root / "project.json").read_bytes()
                if content is not None:
                    write_text(root, "bad-catalog.json", content)
                with patch.object(outline, "CATALOG", root / "bad-catalog.json"):
                    with self.assertRaises(core.OperationError) as caught:
                        core.apply(root, change, 0)
                    self.assertEqual((root / "project.json").read_bytes(), before)
                    state = outline.outline_state(str(root), core.load(root))
                    # Invalid student answers still use the common save path.
                    p = core.apply(root, self.change(root, verification="source_located"), 0)
                self.assertEqual(caught.exception.result["commit_state"], "not_committed")
                self.assertEqual(caught.exception.result["reason"], "outline_policy_invalid")
                self.assertEqual(state["state"], "not_bound")
                self.assertTrue(state["reason"])
                self.assertEqual(p["sections"], {})
                # The later invalid-answer save is separate from the rejected request.
                self.assertEqual(p["revision"], 1)

    def test_post_publication_failure_checks_every_seed_file(self):
        core = runtime("gg_core")
        for damage in ("none", "body", "heading", "missing"):
            with self.subTest(damage=damage):
                root = self.make_project()
                change = self.change(root)
                atomic = core.atomic

                def fail_after_publication(path, data):
                    atomic(path, data)
                    if path == root / "project.json":
                        project = json.loads(data)
                        last = list(project["sections"].values())[-1]
                        target = root / last["path"]
                        if damage == "body":
                            write_text(root, last["path"], "unregistered edit")
                        elif damage == "heading":
                            write_text(root, last["path"], "changed heading\n" + target.read_text(encoding="utf-8"))
                        elif damage == "missing":
                            target.unlink()
                        raise OSError("post-publication failure")

                with patch.object(core, "atomic", side_effect=fail_after_publication):
                    with self.assertRaises(core.OperationError) as caught:
                        core.apply(root, change, 0)
                result = caught.exception.result
                self.assertEqual(result["commit_state"],
                                 "committed_cleanup_pending" if damage == "none" else "indeterminate")
                self.assertEqual(result["reason"], "canonical_commit_failed")
                self.assertEqual((result["revision"], result["request_id"]), (1, "bind"))
                self.assertEqual(core.load(root)["requests"]["bind"]["hash"], core.digest(change))

    def test_release_failure_checks_full_seed_hash(self):
        core, lock = runtime("gg_core"), runtime("gg_lock")
        root = self.make_project()
        change = self.change(root)

        class CleanupFailure(lock.Lock):
            def __exit__(self, *exc):
                super().__exit__(*exc)
                section = list(core.load(root)["sections"].values())[-1]
                target = root / section["path"]
                write_text(root, section["path"], "changed heading\n" + target.read_text(encoding="utf-8"))
                raise lock.LockCleanupError([OSError("synthetic")], release_confirmed=True)

        with patch.object(lock, "Lock", CleanupFailure):
            with self.assertRaises(core.OperationError) as caught:
                core.apply(root, change, 0)
        result = caught.exception.result
        self.assertEqual(result["commit_state"], "indeterminate")
        self.assertEqual(result["reason"], "release_cleanup_failed")
        self.assertEqual((result["revision"], result["request_id"]), (1, "bind"))

    def test_idempotent_replay_checks_actual_section_hash(self):
        core = runtime("gg_core")
        for missing in (False, True):
            with self.subTest(missing=missing):
                root = self.make_project()
                change = self.change(root)
                p = core.apply(root, change, 0)
                section = next(iter(p["sections"].values()))
                if missing:
                    (root / section["path"]).unlink()
                else:
                    write_text(root, section["path"], "unregistered edit")
                before = (root / "project.json").read_bytes()
                with self.assertRaises(core.OperationError) as caught:
                    core.apply(root, change, 0)
                result = caught.exception.result
                self.assertEqual(result["commit_state"], "indeterminate")
                self.assertEqual(result["reason"], "outline_committed_mismatch")
                self.assertEqual((result["revision"], result["request_id"]), (1, "bind"))
                self.assertEqual((root / "project.json").read_bytes(), before)
