"""정본 커밋 뒤 곁파일 다시 쓰기 — 등록부·호출기 자체의 시험.

`tests/test_hort_workbook_advice.py`는 워크북 어드바이스 경로를 보고, 이
파일은 그 아래 층(등록부 행·호출기·CLI 잠금)을 본다. 명세 소유:
`DESIGN-SIDECAR-REGISTRY.md` §4-3(앱 계약 조건 둘)·§2-1·§5-3(등록부 튜플)·
§4-5(C-02 교차 잠금).
"""
import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime, write_text

NOTICES = ".knuaf-notices.json"


class SidecarFailureContractTests(ContractCase):
    """§4-3 앱 계약 조건 ① — 곁파일 실패는 커밋을 깨지 않는다."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.notices = runtime("gg_notices")
        self.root = self.make_project()

    def _commit(self, request_id):
        revision = self.core.load(self.root)["revision"]
        return self.core.apply(self.root, {
            "request_id": request_id,
            "ops": [{"collection": "rules", "value": {
                "id": "sidecar_test", "flag": True,
                "source_refs": []}}]}, revision)

    def test_failed_writer_keeps_commit_and_reports_one_line(self):
        """writer가 `{"status":"failed"}` → 커밋 성공 + stderr 1줄 + 파일 없음."""
        self._commit("seed")                      # 정상 커밋 → 곁파일 생성
        target = Path(self.root) / NOTICES
        self.assertTrue(target.is_file())

        before = self.core.load(self.root)["revision"]
        stderr = io.StringIO()
        with mock.patch.object(self.notices, "write_notices",
                               return_value={"status": "failed",
                                             "path": NOTICES,
                                             "error": "synthetic",
                                             "notices": None}):
            with contextlib.redirect_stderr(stderr):
                after = self._commit("sidecar-failure")

        # ㉣ 커밋은 성공 — 곁파일 실패는 부수 실패다.
        self.assertEqual(before + 1, after["revision"])
        # ㉠ stderr는 정확히 한 줄.
        lines = [line for line in stderr.getvalue().splitlines() if line.strip()]
        self.assertEqual(1, len(lines), stderr.getvalue())
        # ㉡ 차단 접두가 아니다 — 앱 envelope가 blocked로 읽으면 안 된다.
        self.assertFalse(lines[0].startswith("BLOCK:"))
        # ㉢ 형식이 §3-2 그대로다.
        self.assertTrue(lines[0].startswith(
            "sidecar %s failed (gg_notices.write_notices)" % NOTICES),
            lines[0])
        # 실패 정책 remove — 기존 알림이 남아 낡은 화면을 만들지 않는다.
        self.assertFalse(target.exists())

    def test_diagnostic_write_failure_keeps_commit_successful(self):
        """I-01 — stderr 읽는 쪽이 닫혀도 커밋은 성공으로 반환된다."""
        import os
        before = self.core.load(self.root)["revision"]
        read_fd, write_fd = os.pipe()
        os.close(read_fd)                     # 읽는 쪽 없음 → 쓰면 BrokenPipe
        pipe = os.fdopen(write_fd, "w", encoding="utf-8", buffering=1)
        try:
            with mock.patch.object(self.notices, "write_notices",
                                   return_value={"status": "failed",
                                                 "path": NOTICES,
                                                 "error": "synthetic",
                                                 "notices": None}):
                with contextlib.redirect_stderr(pipe):
                    after = self._commit("sidecar-closed-pipe")
        finally:
            try:
                pipe.close()
            except OSError:
                pass
        # 진단 출력이 실패해도 반환은 커밋 성공이어야 한다(정본은 이미 섰다).
        self.assertEqual(before + 1, after["revision"])
        self.assertNotIn("commit_state", after)
        self.assertFalse((Path(self.root) / NOTICES).exists())

    def test_multiline_writer_error_stays_one_line_and_never_blocks(self):
        """I-01 — 줄바꿈·'BLOCK:'이 섞인 예외 문자열도 진단은 한 줄이다."""
        before = self.core.load(self.root)["revision"]
        injected = SystemExit("boom\nBLOCK: second\nthird")
        stderr = io.StringIO()
        with mock.patch.object(self.notices, "write_notices",
                               side_effect=injected):
            with contextlib.redirect_stderr(stderr):
                after = self._commit("sidecar-multiline")
        self.assertEqual(before + 1, after["revision"])
        lines = stderr.getvalue().splitlines()
        self.assertEqual(1, len(lines), stderr.getvalue())
        self.assertFalse(lines[0].startswith("BLOCK:"), lines[0])
        self.assertIn("SystemExit", lines[0])
        self.assertIn("BLOCK: second", lines[0])     # 내용은 남되 줄은 하나

    def test_failed_writer_removes_dangling_symlink(self):
        """I-02 — 대상이 없는 링크도 곁파일 자리에서 지워진다."""
        target = Path(self.root) / NOTICES
        if target.is_symlink() or target.exists():
            target.unlink()
        import os
        os.symlink("missing-notices-target.json", target)
        self.assertTrue(target.is_symlink())
        self.assertFalse(target.exists())     # exists()는 링크를 따라간다

        before = self.core.load(self.root)["revision"]
        stderr = io.StringIO()
        with mock.patch.object(self.notices, "write_notices",
                               return_value={"status": "failed",
                                             "path": NOTICES,
                                             "error": "synthetic",
                                             "notices": None}):
            with contextlib.redirect_stderr(stderr):
                after = self._commit("sidecar-dangling")
        self.assertEqual(before + 1, after["revision"])
        self.assertFalse(target.is_symlink())
        self.assertFalse(target.exists())
        lines = [l for l in stderr.getvalue().splitlines() if l.strip()]
        self.assertEqual(1, len(lines), stderr.getvalue())

    def test_choose_return_carries_no_commit_state(self):
        """§4-3 ② — `commit_state` 키가 없다(앱은 이 키로 ok:false를 판단)."""
        bind_major(self.root, "industrial_insects")
        identity = "a" * 64
        row = {"check_id": "finance_workbook_notice", "title": "t",
               "items": [], "notice_identity": identity,
               "receipt_path": None, "record_refs": [],
               "choices": [{"value": "footnote", "label": "각주"}]}
        with mock.patch.object(self.notices, "notice_rows",
                               return_value=[row]):
            result = self.notices.choose(self.root, identity, "footnote")
        self.assertEqual("chosen", result["status"])
        self.assertNotIn("commit_state", result)


class SidecarRegistryShapeTests(ContractCase):
    """§5-3 ③ — 5튜플 풀기 자리 둘(`init` 루프·이관 파생 집합)을 함께 지난다."""

    def setUp(self):
        self.core = runtime("gg_core")

    def test_init_and_migrate_unpack_five_tuple_rows(self):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-sidecar-")
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)

        # ① 호출기 루프 — init의 첫 커밋이 곁파일을 쓴다.
        fresh = base / "fresh"
        project = self.core.init(fresh)
        self.assertEqual(0, project["revision"])
        self.assertTrue((fresh / NOTICES).is_file())

        # ② 이관 파생 집합 — 원본의 곁파일은 '원자료'로 색인되지 않는다.
        src = base / "legacy"
        (src / "sections").mkdir(parents=True)
        (src / "sections" / "01-개요.md").write_text("# 개요\n본문\n",
                                                     encoding="utf-8")
        (src / NOTICES).write_text(
            json.dumps({"schema": "knuaf.notices/1", "notices": []},
                       ensure_ascii=False), encoding="utf-8")
        dest = base / "dest"
        migrated = self.core.migrate(src, dest, offline_confirmed=True)
        sources = migrated["sources"]
        self.assertNotIn("legacy:" + NOTICES, sources)
        self.assertIn("legacy:sections/01-개요.md", sources)


class SidecarLockTests(ContractCase):
    """§4-5 C-02 교차 잠금 — 두 방향, 대기 없음, 낡은 판 없음."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.notices = runtime("gg_notices")
        self.root = self.make_project()
        bind_major(self.root, "industrial_insects")

    def _slow_rows(self, started):
        """로드한 revision을 제목에 박고 잠깐 붙잡는 행 생성기."""
        core = self.core

        def rows(root):
            revision = core.load(root)["revision"]
            started.set()
            time.sleep(0.35)
            return [{"check_id": "sidecar_race", "title": "rev%d" % revision,
                     "items": [], "notice_identity": "b" * 64,
                     "receipt_path": None, "record_refs": []}]
        return rows

    def _apply(self):
        revision = self.core.load(self.root)["revision"]
        return self.core.apply(self.root, {
            "request_id": "race-apply", "ops": [{"collection": "rules", "value": {
                "id": "race_rule", "flag": True, "source_refs": []}}]},
            revision)

    def test_manual_write_and_canonical_apply_never_overlap(self):
        """방향 1: A(수동 write)가 잠금을 쥐면 B(apply)가 즉시 busy로 끝난다."""
        started = threading.Event()
        holder_error = []
        with mock.patch.object(self.notices, "notice_rows",
                               self._slow_rows(started)):
            def hold():
                try:
                    with self.core.Lock(Path(self.root)):
                        self.notices.write_notices(Path(self.root))
                except BaseException as error:      # 시험 실패를 삼키지 않는다
                    holder_error.append(error)

            holder = threading.Thread(target=hold)
            holder.start()
            self.assertTrue(started.wait(5), "행 생성기가 시작되지 않음")
            revision = self.core.load(self.root)["revision"]

            # ㉠ 대기 없이 LockError(busy)로 끝난다.
            clock = time.monotonic()
            with self.assertRaises(Exception) as caught:
                self._apply()
            elapsed = time.monotonic() - clock
            holder.join(10)

        self.assertEqual([], holder_error)
        self.assertEqual("LockError", type(caught.exception).__name__)
        self.assertEqual("busy", caught.exception.status)
        self.assertLess(elapsed, 0.5)
        # ㉡ 낡은 판이 남지 않았다 — 파일 표식 revision == 지금 정본 revision.
        self.assertEqual(revision, self.core.load(self.root)["revision"])
        document = json.loads((Path(self.root) / NOTICES).read_text(
            encoding="utf-8"))
        self.assertEqual("rev%d" % revision, document["notices"][0]["title"])

    def test_cli_write_refuses_while_canonical_lock_is_held(self):
        """방향 2: B(정본 잠금)가 쥐면 A(CLI write)가 즉시 2로 닫힌다."""
        target = Path(self.root) / NOTICES
        self.notices.write_notices(Path(self.root))     # 비교할 기존 파일
        before = target.read_bytes()
        outcome = {}

        def cli():
            stream = io.StringIO()
            clock = time.monotonic()
            with contextlib.redirect_stdout(stream):
                code = self.notices.main(["write", str(self.root)])
            outcome.update(code=code, out=stream.getvalue(),
                           elapsed=time.monotonic() - clock)

        with self.core.Lock(Path(self.root)):
            worker = threading.Thread(target=cli)
            worker.start()
            worker.join(10)
            self.assertFalse(worker.is_alive())
            # 잠금 안에서 정본은 그대로다(경쟁 쪽이 커밋하지 못했다).
            self.assertEqual(before, target.read_bytes())

        # ㉢ CLI는 exit 2 + busy 사유, 파일 바이트 불변.
        self.assertEqual(2, outcome["code"])
        self.assertEqual({"status": "error", "reason": "busy: guard_busy"},
                         json.loads(outcome["out"]))
        self.assertLess(outcome["elapsed"], 0.5)
        self.assertEqual(before, target.read_bytes())


class SidecarContractUnitTests(ContractCase):
    """§3-1 성공 판정 — 반환값 계약 두 종."""

    def test_sidecar_ok_contracts(self):
        core = runtime("gg_core")
        self.assertIs(True, core._sidecar_ok({"status": "written"}, "dict"))
        self.assertIs(False, core._sidecar_ok(None, "dict"))
        self.assertIs(False, core._sidecar_ok({"status": "written"}, "none"))


if __name__ == "__main__":
    unittest.main()
