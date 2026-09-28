"""학생 신고 CLI(gg_report) 계약 시험 — 설계 r1 + r2(D1-01~06) + r3.

실제 Worker에는 보내지 않는다. 127.0.0.1 임의 포트의 http.server가
src/index.js와 같은 JSON 응답을 흉내 낸다. draft는
KNUAF_REPORT_TEST_ENDPOINT(loopback)로 초안에 수신처를 잠그고,
send는 초안에 기록된 수신처로만 보낸다. 모든 임시 파일은
tempfile.TemporaryDirectory 아래에 둔다.
"""
import getpass
import hashlib
import http.server
import importlib.util
import io
import json
import os
import re
import socket
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, REPO_ROOT, runtime


# 배포 Worker(src/index.js)의 PII_PATTERNS를 Python으로 옮긴 시험 헬퍼.
# JS \\b(ASCII 단어 경계)를 흉내 내려고 re.ASCII를 쓴다. 이 헬퍼는
# 로컬 가림 뒤 남은 문자열에 Worker 가림이 더 할 일이 없는지 보는
# 보조 확인이며 Worker 동작과 완전히 같다는 증거는 아니다.
_WORKER_PII = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
     "[이메일 가림]"),
    (re.compile(r"\b01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}\b", re.ASCII),
     "[전화번호 가림]"),
    (re.compile(r"\b\d{6}[-\s]?[1-4]\d{6}\b", re.ASCII), "[주민번호 가림]"),
    (re.compile(r"\b(19|20)\d{6}\b", re.ASCII), "[학번 가림]"),
)


def _worker_redact(text):
    for pattern, label in _WORKER_PII:
        text = pattern.sub(label, text)
    return text


class _WorkerServer:
    """src/index.js와 같은 형태의 JSON 응답을 내는 loopback 가짜 Worker.

    responder(handler) -> (status, dict) | "drop". 요청은
    (path, body_bytes, content_type, user_agent)로 기록한다.
    """

    def __init__(self, case, responder):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                outer.requests.append((
                    self.path, body,
                    self.headers.get("Content-Type"),
                    self.headers.get("User-Agent")))
                result = responder(self)
                if result == "drop":
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    self.close_connection = True
                    return
                status, payload = result
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        case.addCleanup(self.server.server_close)
        case.addCleanup(self.server.shutdown)

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]


class _ReportCase(ContractCase):
    def setUp(self):
        self.gm = runtime("gg_report")
        self._tmp = tempfile.TemporaryDirectory(prefix="knuaf-report-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ)
        self._env.start()
        self.addCleanup(self._env.stop)
        os.environ.pop(self.gm.TEST_ENDPOINT_ENV, None)

    def _set_test_env(self, base):
        if base is None:
            os.environ.pop(self.gm.TEST_ENDPOINT_ENV, None)
        else:
            os.environ[self.gm.TEST_ENDPOINT_ENV] = base

    def _cli(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.gm.main(argv)
        return code, json.loads(buf.getvalue())

    def _in_doc(self, kind="issue", **fields):
        doc = {"kind": kind, "category": "bug",
               "title": "테스트 제목", "description": "설명입니다"}
        doc.update(fields)
        return doc

    def _build(self, kind="issue", **fields):
        return self.gm.build_draft(
            self._in_doc(kind, **fields), plugin_root=REPO_ROOT)

    def _error_of(self, kind="issue", **fields):
        with self.assertRaises(self.gm.ReportError) as caught:
            self._build(kind, **fields)
        return caught.exception

    def _write_input(self, name, doc):
        path = self.tmp / ("%s.in.json" % name)
        path.write_text(json.dumps(doc, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def _draft_file(self, name="r", kind="issue", **fields):
        in_path = self._write_input(name, self._in_doc(kind, **fields))
        out_path = self.tmp / ("%s.draft.json" % name)
        code, result = self._cli(
            ["draft", "--input", str(in_path), "--out", str(out_path)])
        self.assertEqual(0, code, result)
        self.assertEqual("drafted", result["status"])
        return out_path, result["sha256"], result

    def _rewrite_draft(self, path, mutate):
        doc = json.loads(path.read_text(encoding="utf-8"))
        mutate(doc)
        text = json.dumps(doc, ensure_ascii=False, indent=2,
                          sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _sha_of(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _sending(self, path, pattern):
        path = Path(path)
        return sorted(path.parent.glob(path.name + pattern))

    def _assert_fully_masked(self, text):
        """로컬 재검사 + Worker 가림 포트 뒤에도 아무것도 안 남는지."""
        masked, counts = self.gm.redact(text)
        self.assertTrue(any(counts.values()),
                        "expected at least one redaction in %r" % text)
        again, counts2 = self.gm.redact(masked)
        self.assertEqual(masked, again)
        self.assertFalse(any(counts2.values()),
                         "mask tokens must not re-trigger: %r" % counts2)
        self.assertEqual(masked, _worker_redact(masked))
        return masked


class InputValidationTests(_ReportCase):
    def test_unknown_field_rejected(self):
        exc = self._error_of(extra=1)
        self.assertEqual("unknown_field", exc.code)

    def test_kind_enum_and_unknown_category_rejected(self):
        self.assertEqual("invalid_kind", self._error_of(kind="x").code)
        # 모르는 category도 거부 — Worker의 조용한 other 변환을 쓰지 않음.
        self.assertEqual(
            "invalid_category", self._error_of(category="x").code)

    def test_non_string_field_rejected(self):
        self.assertEqual(
            "not_string", self._error_of(description=123).code)

    def test_missing_fields(self):
        self.assertEqual(
            "missing_fields", self._error_of(description="  ").code)
        doc = {"kind": "issue", "category": "bug",
               "description": "있음"}
        with self.assertRaises(self.gm.ReportError) as caught:
            self.gm.build_draft(doc, plugin_root=REPO_ROOT)
        self.assertEqual("missing_fields", caught.exception.code)

    def test_title_limit_utf16(self):
        self._build(title="가" * 120)
        self.assertEqual(
            "too_long", self._error_of(title="가" * 121).code)

    def test_issue_local_limits(self):
        self._build(description="가" * 2000)
        self.assertEqual(
            "too_long", self._error_of(description="가" * 2001).code)
        self._build(steps="가" * 2000)
        self.assertEqual(
            "too_long", self._error_of(steps="가" * 2001).code)
        self._build(error_message="가" * 4000)
        self.assertEqual(
            "too_long", self._error_of(error_message="가" * 4001).code)

    def test_private_field_limit_8000(self):
        self._build("private", description="가" * 1500)
        self.assertEqual("private_too_long",
                         self._error_of("private",
                                        description="가" * 8000).code)

    def test_astral_length_boundary(self):
        # 😀는 UTF-16에서 2단위: 60개 = 120 통과, 61개 = 122 거부.
        self._build(title="😀" * 60)
        self.assertEqual(
            "too_long", self._error_of(title="😀" * 61).code)

    def test_private_composite_boundary_1900(self):
        # F1-06: 구현 함수에서 역산하지 않은 독립 기대값. Worker
        # sendPrivate 합성: "**[비공개 신고] " + title + "**\n분류: " +
        # category + "\n\n" + description + "\n버전: " + skill_version.
        version = self._build("private")["payload"]["skill_version"]
        head = "**[비공개 신고] " + "테스트 제목" + "**\n분류: " + "bug" + "\n\n"
        tail = "\n버전: " + version
        fit = 1900 - len(head.encode("utf-16-le")) // 2 \
            - len(tail.encode("utf-16-le")) // 2
        self._build("private", description="한" * (fit - 1))
        self._build("private", description="한" * fit)
        self.assertEqual("private_too_long",
                         self._error_of("private",
                                        description="한" * (fit + 1)).code)
        # 보조 평면 문자는 2단위: 경계 직전에서 한 글자 교체로 넘친다.
        self.assertEqual("private_too_long",
                         self._error_of("private",
                                        description="한" * (fit - 1) + "😀").code)

    def test_private_composite_includes_optional_fields(self):
        version = self._build("private")["payload"]["skill_version"]
        head = "**[비공개 신고] " + "테스트 제목" + "**\n분류: " + "bug" + "\n\n"
        tail = ("\n\n단계: " + "y" + "\n\n오류: " + "z"
                + "\n버전: " + version)
        fit = 1900 - len(head.encode("utf-16-le")) // 2 \
            - len(tail.encode("utf-16-le")) // 2
        self._build("private", description="한" * fit,
                    steps="y", error_message="z")
        self.assertEqual("private_too_long",
                         self._error_of(
                             "private", description="한" * (fit + 1),
                             steps="y", error_message="z").code)

    def test_payload_body_16kb(self):
        # issue 로컬 상한 합계(2000+2000+4000 한글)가 UTF-8로 16KB 초과.
        exc = self._error_of(description="한" * 2000, steps="한" * 2000,
                             error_message="한" * 4000)
        self.assertEqual("too_large", exc.code)

    def test_optional_omitted_fields_pass(self):
        # r3 R2-01: 선택 필드·environment(private)가 없어도 비교 통과.
        private = self._build("private")
        self.assertNotIn("steps", private["payload"])
        self.assertNotIn("environment", private["payload"])
        issue = self._build("issue")
        self.assertNotIn("steps", issue["payload"])
        self.assertNotIn("error_message", issue["payload"])
        self.assertIn("environment", issue["payload"])

    def test_empty_optional_field_dropped(self):
        draft = self._build(steps="   ")
        self.assertNotIn("steps", draft["payload"])

    def test_values_are_trimmed(self):
        draft = self._build(title="  제목  ", description=" 설명 ")
        self.assertEqual("제목", draft["payload"]["title"])
        self.assertEqual("설명", draft["payload"]["description"])

    def test_skill_version_and_environment_shape(self):
        draft = self._build()
        installed_version = json.loads((REPO_ROOT / "skills" / "knuaf-doc" /
            "version.json").read_text(encoding="utf-8"))["version"]
        self.assertEqual("knuaf-doc " + installed_version,
                         draft["payload"]["skill_version"])
        env = draft["payload"]["environment"]
        self.assertNotIn(socket.gethostname(), env)
        self.assertNotIn(getpass.getuser(), env)
        self.assertNotIn(os.getcwd(), env)
        self.assertNotIn("/", env)

    def test_skill_version_unknown_without_plugin(self):
        draft = self.gm.build_draft(
            self._in_doc(), plugin_root=self.tmp, skill_root=self.tmp)
        self.assertEqual("knuaf-doc unknown",
                         draft["payload"]["skill_version"])

    def test_copy_install_uses_its_own_version_without_plugin_json(self):
        copied = self.tmp / "codex-home" / "skills" / "knuaf-doc"
        (copied / "scripts").mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "skills" / "knuaf-doc" / "version.json",
                     copied / "version.json")
        script = copied / "scripts" / "gg_report.py"
        shutil.copy2(REPO_ROOT / "skills" / "knuaf-doc" / "scripts" /
                     "gg_report.py", script)
        spec = importlib.util.spec_from_file_location("copied_gg_report", script)
        copied_report = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(copied_report)
        finally:
            sys.dont_write_bytecode = previous
        expected = json.loads((copied / "version.json").read_text(
            encoding="utf-8"))["version"]
        draft = copied_report.build_draft(self._in_doc(), plugin_root=self.tmp)
        self.assertEqual("knuaf-doc " + expected,
                         draft["payload"]["skill_version"])

    def test_invalid_copy_version_uses_plugin_fallback(self):
        copied = self.tmp / "skills" / "knuaf-doc"
        copied.mkdir(parents=True)
        plugin_dir = self.tmp / ".codex-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"version": "0.2.0"}), encoding="utf-8")
        for raw in ('{"schema":"knuaf-doc/version@1","repo":'
                    '"starceas/knuaf-doc","version":"01.1.0"}',
                    '{"schema":"knuaf-doc/version@1","repo":"other",'
                    '"version":"0.1.1"}',
                    '{"schema":"knuaf-doc/version@1","repo":'
                    '"starceas/knuaf-doc","version":"0.1.1",'
                    '"version":"0.1.2"}'):
            (copied / "version.json").write_text(raw, encoding="utf-8")
            draft = self.gm.build_draft(self._in_doc(), plugin_root=self.tmp,
                                        skill_root=copied)
            self.assertEqual("knuaf-doc 0.2.0",
                             draft["payload"]["skill_version"])


class RedactionTests(_ReportCase):
    def test_quoted_path_with_spaces(self):
        masked = self._assert_fully_masked(
            '로그 "/Users/hong/나의 폴더/log.txt" 끝')
        self.assertEqual('로그 "[경로 가림]" 끝', masked)
        self.assertNotIn("나의 폴더", masked)

    def test_unquoted_path_masks_to_line_end(self):
        masked = self._assert_fully_masked(
            "열었던 /Users/hong/na/pri vate.txt 에서 멈춤\n다음 줄")
        self.assertEqual("열었던 [경로 가림]\n다음 줄", masked)

    def test_windows_and_unc_paths(self):
        masked = self._assert_fully_masked(
            "경로 C:\\Users\\Hong\\My Documents\\a.txt\n다음")
        self.assertEqual("경로 [경로 가림]\n다음", masked)
        masked = self._assert_fully_masked("C:/x/y.txt 에러")
        self.assertEqual("[경로 가림]", masked)
        masked = self._assert_fully_masked(
            "공유 \\\\서버\\공유\\파일.hwp 확인")
        self.assertIn("[경로 가림]", masked)
        self.assertNotIn("서버", masked)

    def test_path_outside_known_prefixes(self):
        masked = self._assert_fully_masked("/opt/homebrew/bin/tool 실패")
        self.assertEqual("[경로 가림]", masked)

    def test_home_tilde_path(self):
        masked = self._assert_fully_masked("열기 ~/Documents/논문.docx 끝")
        self.assertEqual("열기 [경로 가림]", masked)

    def test_url_not_masked_as_path(self):
        masked, counts = self.gm.redact(
            "참고 https://example.com/a/b 입니다")
        self.assertEqual("참고 https://example.com/a/b 입니다", masked)
        self.assertEqual(0, counts["path"])

    def test_filenames_masked(self):
        masked = self._assert_fully_masked("파일 내 논문.hwpx 열기")
        self.assertEqual("파일 내 [파일명 가림] 열기", masked)
        masked = self._assert_fully_masked(
            '첨부 "보고서 초안.docx" 확인')
        self.assertEqual('첨부 "[파일명 가림]" 확인', masked)
        masked = self._assert_fully_masked("사본 최종본.v2.docx 저장")
        self.assertEqual("사본 [파일명 가림] 저장", masked)

    def test_phones_masked(self):
        for raw in ("010-1234-5678", "010.1234.5678", "01012345678",
                    "010 1234 5678", "02-123-4567", "02)1234-5678",
                    "02 1234 5678", "0212345678", "0311234567"):
            masked = self._assert_fully_masked("연락 " + raw + " 끝")
            self.assertNotIn("1234", masked, raw)
            self.assertIn("[전화번호 가림]", masked, raw)

    def test_student_id_patterns(self):
        masked = self._assert_fully_masked("번호 20241234 확인")
        self.assertEqual("번호 [학번 가림] 확인", masked)
        masked = self._assert_fully_masked("학번 2412345 등록")
        self.assertEqual("학번 [학번 가림] 등록", masked)
        # 8자리 앞이 숫자면 학번이 아니다(숫자 경계).
        masked, counts = self.gm.redact("값 120241234 끝")
        self.assertIn("120241234", masked)

    def test_eight_digit_diagnostic_value_masked(self):
        # r2 D1-06 회귀: 날짜·크기 같은 8자리 숫자도 가려진다.
        masked = self._assert_fully_masked("파일 크기 20260927바이트")
        self.assertEqual("파일 크기 [학번 가림]바이트", masked)

    def test_email_and_resident_number(self):
        masked = self._assert_fully_masked("메일 a@b.co 회신")
        self.assertEqual("메일 [이메일 가림] 회신", masked)
        masked = self._assert_fully_masked("번호 900101-1234567 등록")
        self.assertEqual("번호 [주민번호 가림] 등록", masked)

    def test_korean_adjacent_boundaries(self):
        masked = self._assert_fully_masked("전화010-1234-5678번")
        self.assertEqual("전화[전화번호 가림]번", masked)

    def test_warnings_flag_personal_words(self):
        draft = self._build(description="연락처 010-1234-5678로 주세요")
        words = {w["word"] for w in draft["warnings"]}
        self.assertIn("연락처", words)
        draft = self._build(description="학번 2412345")
        words = {w["word"] for w in draft["warnings"]}
        self.assertIn("학번", words)

    def test_mask_tokens_do_not_self_warn(self):
        # 가림 표식 "[전화번호 가림]" 안의 "전화"는 경고로 잡히지 않아야 한다.
        draft = self._build(description="010-1234-5678")
        words = {w["word"] for w in draft["warnings"]}
        self.assertNotIn("전화", words)

    def test_private_not_redacted(self):
        draft = self._build("private", description="전화 010-1234-5678")
        self.assertIn("010-1234-5678", draft["payload"]["description"])
        self.assertEqual([], draft["redactions"])
        self.assertEqual([], draft["warnings"])

    def test_redaction_counts_per_field(self):
        draft = self._build(
            description="a@b.co 010-1234-5678",
            error_message="/tmp/x/y.log")
        by_field = {}
        for r in draft["redactions"]:
            by_field.setdefault(r["field"], {})[r["type"]] = r["count"]
        self.assertEqual(1, by_field["description"]["email"])
        self.assertEqual(1, by_field["description"]["phone"])
        self.assertEqual(1, by_field["error_message"]["path"])

    def test_filenames_followed_by_punctuation_l1_1(self):
        masked = self._assert_fully_masked("(가상학생_원고.docx)")
        self.assertEqual("([파일명 가림])", masked)
        masked = self._assert_fully_masked("원고.docx, 다음")
        self.assertEqual("[파일명 가림], 다음", masked)
        masked = self._assert_fully_masked("원고.hwp.")
        self.assertEqual("[파일명 가림].", masked)
        # 한글 조사가 붙어도 계속 가린다.
        masked = self._assert_fully_masked("원고.docx를 저장")
        self.assertEqual("[파일명 가림]를 저장", masked)
        masked = self._assert_fully_masked("파일명=보고서.hwp 등록")
        self.assertIn("[파일명 가림]", masked)
        self.assertNotIn("보고서", masked)
        masked = self._assert_fully_masked("목록:초안.docx 확인")
        self.assertIn("[파일명 가림]", masked)

    def test_path_boundary_separators_l1_2(self):
        masked = self._assert_fully_masked(
            "path=/Users/demo/홍길동/work")
        self.assertEqual("path=[경로 가림]", masked)
        masked = self._assert_fully_masked("파일:/home/demo/x/y")
        self.assertEqual("파일:[경로 가림]", masked)
        masked = self._assert_fully_masked("k=/opt/homebrew/lib/x")
        self.assertEqual("k=[경로 가림]", masked)
        masked = self._assert_fully_masked(
            "목록,/var/folders/ab/cd/파일 저장")
        self.assertEqual("목록,[경로 가림]", masked)

    def test_always_path_prefixes_without_boundary_l1_2(self):
        masked = self._assert_fully_masked("폴더/Users/demo/x 열기")
        self.assertEqual("폴더[경로 가림]", masked)
        masked = self._assert_fully_masked(
            "파일C:\\Users\\demo\\x 다음")
        self.assertEqual("파일[경로 가림]", masked)
        masked = self._assert_fully_masked("변수=D:/Users/demo/x 끝")
        self.assertEqual("변수=[경로 가림]", masked)
        masked = self._assert_fully_masked(
            "외장/Volumes/hdd/doc/x 확인")
        self.assertEqual("외장[경로 가림]", masked)

    def test_lone_slash_is_not_a_path_l1_3(self):
        text = "크기 / 너비 / 높이"
        masked, counts = self.gm.redact(text)
        self.assertEqual(text, masked)
        self.assertEqual(0, counts["path"])
        masked, counts = self.gm.redact("a /b /c")
        # "/b" 뒤에 "/"가 하나 더 있으면 경로 시작으로 본다(L1-2 경계 허용).
        self.assertEqual("a [경로 가림]", masked)
        self.assertEqual(1, counts["path"])
        masked, counts = self.gm.redact("a / b")
        self.assertEqual("a / b", masked)
        masked, counts = self.gm.redact("메모//주석 같은 글")
        self.assertEqual("메모//주석 같은 글", masked)


class DraftCliTests(_ReportCase):
    def test_draft_sha_matches_bytes_under_crlf_text_mode_f1_05(self):
        # F1-05: Windows 텍스트 모드처럼 줄바꿈을 CRLF로 바꾸는 open을
        # 주입해도 draft 출력 sha = 저장 바이트 sha이고 send 확인이 통과한다.
        import builtins
        real_open = builtins.open

        def crlf_open(file, mode="r", *args, **kwargs):
            if "b" not in mode and any(c in mode for c in "wxa"):
                kwargs.setdefault("newline", "\r\n")
            return real_open(file, mode, *args, **kwargs)

        server = _WorkerServer(self, lambda h: (201, {
            "ok": True, "kind": "private"}))
        self._set_test_env(server.base)
        with mock.patch.object(builtins, "open", crlf_open):
            path, sha, _ = self._draft_file(kind="private")
            self.assertEqual(sha, self._sha_of(path))
            self.assertNotIn(b"\r", path.read_bytes())
            self.assertEqual("sent", self.gm.send(path, sha)["status"])

    def test_draft_cli_writes_schema_and_sha(self):
        path, sha, result = self._draft_file()
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(self.gm.DRAFT_SCHEMA, doc["schema"])
        self.assertEqual(sha, self._sha_of(path))
        self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))
        self.assertEqual(self.gm.PRODUCT_ENDPOINT,
                         doc["destination"]["endpoint"])
        self.assertFalse(doc["destination"]["test_mode"])
        self.assertFalse(result["test_mode"])

    def test_out_exists_refused(self):
        out = self.tmp / "dup.json"
        out.write_text("{}", encoding="utf-8")
        in_path = self._write_input("in", self._in_doc())
        code, result = self._cli(
            ["draft", "--input", str(in_path), "--out", str(out)])
        self.assertEqual(2, code)
        self.assertEqual("out_exists", result["error"])

    def test_out_parent_missing(self):
        in_path = self._write_input("in", self._in_doc())
        code, result = self._cli(
            ["draft", "--input", str(in_path),
             "--out", str(self.tmp / "nope" / "d.json")])
        self.assertEqual(2, code)
        self.assertEqual("out_parent_missing", result["error"])

    def test_input_unreadable(self):
        code, result = self._cli(
            ["draft", "--input", str(self.tmp / "none.json"),
             "--out", str(self.tmp / "d.json")])
        self.assertEqual(2, code)
        self.assertEqual("cannot_read_input", result["error"])

    def test_issue_preview_text(self):
        _, _, result = self._draft_file()
        preview = result["preview"]
        self.assertIn("보낼 곳: 공개 GitHub 이슈(starceas/knuaf-doc)",
                      preview)
        self.assertIn("공개 이슈라 누구나 볼 수 있습니다.", preview)
        self.assertIn("분류: 오류", preview)
        self.assertIn("제목: 테스트 제목", preview)
        self.assertIn("내용:\n설명입니다", preview)
        version = json.loads((REPO_ROOT / "skills" / "knuaf-doc" /
            "version.json").read_text(encoding="utf-8"))["version"]
        self.assertIn("함께 보내는 정보: 스킬 버전 knuaf-doc " + version + ", 환경",
                      preview)

    def test_private_preview_shows_only_sent_fields(self):
        _, _, result = self._draft_file(kind="private")
        preview = result["preview"]
        self.assertIn("보낼 곳: 개발자 비공개 채널", preview)
        self.assertNotIn("공개 이슈라 누구나 볼 수 있습니다.", preview)
        version = json.loads((REPO_ROOT / "skills" / "knuaf-doc" /
            "version.json").read_text(encoding="utf-8"))["version"]
        self.assertIn("함께 보내는 정보: 스킬 버전 knuaf-doc " + version,
                      preview)
        self.assertNotIn("환경", preview)

    def test_show_command(self):
        path, sha, _ = self._draft_file()
        code, result = self._cli(["show", "--draft", str(path)])
        self.assertEqual(0, code)
        self.assertEqual("draft", result["status"])
        self.assertEqual(sha, result["sha256"])
        self.assertIsNone(result["sent"])
        self.assertIn("보낼 곳:", result["preview"])

    def test_show_rejects_tampered_draft(self):
        path, sha, _ = self._draft_file()
        self._rewrite_draft(
            path, lambda d: d["payload"].__setitem__("title", "바꿈 "))
        code, result = self._cli(["show", "--draft", str(path)])
        self.assertEqual(2, code)
        self.assertIn(result["error"],
                      ("invalid_draft", "worker_would_alter"))


class EndpointTests(_ReportCase):
    def test_test_endpoint_recorded_in_draft(self):
        server = _WorkerServer(
            self, lambda h: (201, {"ok": True, "kind": "issue",
                                   "number": 1, "url": "https://x/1"}))
        self._set_test_env(server.base)
        path, _, result = self._draft_file()
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            {"endpoint": server.base + "/report", "test_mode": True},
            doc["destination"])
        self.assertTrue(result["test_mode"])
        self.assertTrue(result["preview"].startswith(
            "[시험 대상] " + server.base + "/report"))

    def test_test_endpoint_rejects_non_loopback(self):
        self._set_test_env("http://example.com:8080")
        exc = self._error_of()
        self.assertEqual("invalid_endpoint", exc.code)

    def test_test_endpoint_rejects_extra_path(self):
        self._set_test_env("http://127.0.0.1:8080/x")
        exc = self._error_of()
        self.assertEqual("invalid_endpoint", exc.code)

    def test_send_endpoint_changed_env_differs(self):
        server = _WorkerServer(
            self, lambda h: (201, {"ok": True, "kind": "private"}))
        self._set_test_env(server.base)
        path, sha, _ = self._draft_file(kind="private")
        self._set_test_env("http://127.0.0.1:9")
        result = self.gm.send(path, sha)
        self.assertEqual("endpoint_changed", result["error"])
        self.assertEqual(0, len(server.requests))
        # 초안은 시험 모드인데 env가 없어도 거부.
        self._set_test_env(None)
        result = self.gm.send(path, sha)
        self.assertEqual("endpoint_changed", result["error"])

    def test_send_product_draft_with_env_set_changed(self):
        path, sha, _ = self._draft_file()
        self._set_test_env("http://127.0.0.1:9")
        result = self.gm.send(path, sha)
        self.assertEqual("endpoint_changed", result["error"])

    def test_send_endpoint_arg_must_match_recorded(self):
        server = _WorkerServer(
            self, lambda h: (201, {"ok": True, "kind": "private"}))
        self._set_test_env(server.base)
        path, sha, _ = self._draft_file(kind="private")
        result = self.gm.send(path, sha, endpoint="http://127.0.0.1:9/report")
        self.assertEqual("endpoint_changed", result["error"])
        result = self.gm.send(path, sha, endpoint=server.base + "/report")
        self.assertEqual("sent", result["status"])

    def test_tampered_recorded_endpoint_invalid_draft(self):
        server = _WorkerServer(
            self, lambda h: (201, {"ok": True, "kind": "private"}))
        self._set_test_env(server.base)
        path, sha, _ = self._draft_file(kind="private")
        sha = self._rewrite_draft(
            path, lambda d: d["destination"].update(
                endpoint="https://evil.example/report"))
        result = self.gm.send(path, sha)
        self.assertEqual("invalid_draft", result["error"])
        self.assertEqual(0, len(server.requests))


class DraftRevalidationTests(_ReportCase):
    def _send_error(self, path, sha):
        return self.gm.send(path, sha)

    def test_confirm_mismatch(self):
        path, sha, _ = self._draft_file()
        result = self.gm.send(path, "0" * 64)
        self.assertEqual("confirm_mismatch", result["error"])
        self.assertEqual(sha, result["sha256"])

    def test_empty_string_field_invalid(self):
        # r3 R2-01: payload에 있는 필드는 빈 문자열이면 안 된다.
        path, _, _ = self._draft_file()
        sha = self._rewrite_draft(
            path, lambda d: d["payload"].__setitem__("steps", ""))
        self.assertEqual("invalid_draft",
                         self._send_error(path, sha)["error"])

    def test_worker_would_alter_on_untrimmed_title(self):
        path, _, _ = self._draft_file()
        sha = self._rewrite_draft(
            path, lambda d: d["payload"].__setitem__("title", "제목 "))
        self.assertEqual("worker_would_alter",
                         self._send_error(path, sha)["error"])

    def test_missing_environment_in_issue_invalid(self):
        path, _, _ = self._draft_file()
        sha = self._rewrite_draft(
            path, lambda d: d["payload"].pop("environment"))
        self.assertEqual("invalid_draft",
                         self._send_error(path, sha)["error"])

    def test_environment_in_private_invalid(self):
        path, _, _ = self._draft_file(kind="private")
        sha = self._rewrite_draft(
            path, lambda d: d["payload"].__setitem__("environment", "x"))
        self.assertEqual("invalid_draft",
                         self._send_error(path, sha)["error"])

    def test_pii_remaining_blocks_send(self):
        server = _WorkerServer(
            self, lambda h: (201, {"ok": True, "kind": "issue",
                                   "number": 1, "url": "https://x/1"}))
        self._set_test_env(server.base)
        path, _, _ = self._draft_file()
        sha = self._rewrite_draft(
            path, lambda d: d["payload"].__setitem__(
                "description",
                d["payload"]["description"] + " 010-9999-8888"))
        self.assertEqual("pii_remaining",
                         self._send_error(path, sha)["error"])
        self.assertEqual(0, len(server.requests))

    def test_unknown_payload_key_invalid(self):
        path, _, _ = self._draft_file()
        sha = self._rewrite_draft(
            path, lambda d: d["payload"].__setitem__("extra", "x"))
        self.assertEqual("invalid_draft",
                         self._send_error(path, sha)["error"])

    def test_metadata_fields_rescanned_f1_01(self):
        # F1-01: environment·skill_version도 공개 재검사 대상 — 거부, 요청 0회.
        server = _WorkerServer(
            self, lambda h: (201, {"ok": True, "kind": "issue",
                                   "number": 1, "url": "https://x/1"}))
        self._set_test_env(server.base)
        for field, value in (
                ("environment", "Darwin 1 arm64 /Users/demo/가상학생/notes"),
                ("skill_version", "knuaf-doc 가상.docx"),
                ("environment", "Linux; 010-1234-5678")):
            with self.subTest(field=field, value=value):
                path, _, _ = self._draft_file(name="m%d" % len(value))
                sha = self._rewrite_draft(
                    path, lambda d: d["payload"].__setitem__(field, value))
                result = self._send_error(path, sha)
                self.assertEqual("pii_remaining", result["error"])
                self.assertEqual(field, result["field"])
        self.assertEqual(0, len(server.requests))

    def test_js_trim_characters_f1_02(self):
        # F1-02: Worker(JS) trim은 U+FEFF를 지운다 — 로컬도 같게 정규화하고,
        # 손으로 넣은 U+FEFF는 worker_would_alter로 거부한다.
        draft = self._build(title="\ufeff재현\ufeff")
        self.assertEqual("재현", draft["payload"]["title"])
        self.assertEqual("missing_fields",
                         self._error_of(title="\ufeff").code)
        # JS trim이 지우지 않는 \x1c는 Python strip과 달리 그대로 둔다.
        draft = self._build(title="\x1c제목")
        self.assertEqual("\x1c제목", draft["payload"]["title"])
        path, _, _ = self._draft_file()
        sha = self._rewrite_draft(
            path, lambda d: d["payload"].__setitem__("title", "\ufeff재현"))
        self.assertEqual("worker_would_alter",
                         self._send_error(path, sha)["error"])


class SendTests(_ReportCase):
    def _server(self, responder):
        server = _WorkerServer(self, responder)
        self._set_test_env(server.base)
        return server

    def test_send_201_issue_receipt_and_done(self):
        server = self._server(lambda h: (201, {
            "ok": True, "kind": "issue", "number": 12,
            "url": "https://github.com/starceas/knuaf-doc/issues/12"}))
        path, sha, _ = self._draft_file()
        result = self.gm.send(path, sha)
        self.assertEqual("sent", result["status"])
        self.assertEqual("issue", result["kind"])
        self.assertEqual(12, result["number"])
        self.assertTrue(result["test_mode"])
        self.assertIn("공개 이슈 #12로 접수됐습니다", result["message"])
        self.assertEqual(1, len(server.requests))
        req_path, body, ctype, ua = server.requests[0]
        self.assertEqual("/report", req_path)
        self.assertEqual("application/json; charset=utf-8", ctype)
        self.assertEqual("knuaf-doc-report/1", ua)
        payload = json.loads(path.read_text(
            encoding="utf-8"))["payload"]
        self.assertEqual(payload, json.loads(body.decode("utf-8")))

        receipt = json.loads(
            Path(str(path) + ".receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(self.gm.RECEIPT_SCHEMA, receipt["schema"])
        self.assertEqual(sha, receipt["draft_sha256"])
        self.assertEqual(12, receipt["number"])
        self.assertTrue(receipt["test_mode"])
        self.assertFalse(Path(str(path) + ".sending.json").exists())
        self.assertTrue(self._sending(path, ".sending.*.done.json"))

    def test_send_201_private(self):
        server = self._server(
            lambda h: (201, {"ok": True, "kind": "private"}))
        path, sha, _ = self._draft_file(kind="private")
        result = self.gm.send(path, sha)
        self.assertEqual("sent", result["status"])
        self.assertNotIn("number", result)
        self.assertIn("비공개로 전달됐습니다", result["message"])
        receipt = json.loads(
            Path(str(path) + ".receipt.json").read_text(encoding="utf-8"))
        self.assertNotIn("number", receipt)
        self.assertNotIn("url", receipt)

    def test_already_sent_makes_no_request(self):
        server = self._server(lambda h: (201, {
            "ok": True, "kind": "issue", "number": 3,
            "url": "https://github.com/x/3"}))
        path, sha, _ = self._draft_file()
        first = self.gm.send(path, sha)
        self.assertEqual("sent", first["status"])
        second = self.gm.send(path, sha)
        self.assertEqual("already_sent", second["status"])
        self.assertEqual(3, second["number"])
        self.assertEqual(1, len(server.requests))

    def test_rejected_400_and_resend_allowed(self):
        state = {"fail": True}

        def responder(handler):
            if state["fail"]:
                return 400, {"ok": False, "error": "invalid_kind"}
            return 201, {"ok": True, "kind": "issue", "number": 7,
                         "url": "https://github.com/x/7"}

        server = self._server(responder)
        path, sha, _ = self._draft_file()
        result = self.gm.send(path, sha)
        self.assertEqual("rejected", result["status"])
        self.assertEqual("invalid_kind", result["reason"])
        self.assertFalse(Path(str(path) + ".receipt.json").exists())
        self.assertTrue(self._sending(path, ".sending.*.failed.json"))
        self.assertFalse(Path(str(path) + ".sending.json").exists())
        state["fail"] = False
        again = self.gm.send(path, sha)
        self.assertEqual("sent", again["status"])
        self.assertEqual(2, len(server.requests))

    def test_rejected_413(self):
        self._server(lambda h: (413, {"ok": False, "error": "too_large"}))
        path, sha, _ = self._draft_file()
        result = self.gm.send(path, sha)
        self.assertEqual("rejected", result["status"])
        self.assertEqual("too_large", result["reason"])

    def test_rate_limited_429_retry_after(self):
        state = {"limited": True}

        def responder(handler):
            if state["limited"]:
                return 429, {"ok": False, "error": "rate_limited"}
            return 201, {"ok": True, "kind": "private"}

        self._server(responder)
        path, sha, _ = self._draft_file(kind="private")
        result = self.gm.send(path, sha)
        self.assertEqual("not_sent", result["status"])
        self.assertEqual("rate_limited", result["reason"])
        self.assertEqual("1분 뒤", result["retry"])
        self.assertTrue(self._sending(path, ".sending.*.failed.json"))
        state["limited"] = False
        self.assertEqual("sent", self.gm.send(path, sha)["status"])

    def test_server_error_502(self):
        self._server(lambda h: (502, {
            "ok": False, "error": "github_failed", "status": 500}))
        path, sha, _ = self._draft_file()
        result = self.gm.send(path, sha)
        self.assertEqual("not_sent", result["status"])
        self.assertEqual("server_error", result["reason"])
        self.assertEqual(502, result["http_status"])
        self.assertTrue(self._sending(path, ".sending.*.failed.json"))

    def test_redirect_status_is_delivery_unknown(self):
        server = self._server(
            lambda h: (302, {"ok": False, "error": "redirect"}))
        path, sha, _ = self._draft_file()
        result = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", result["status"])
        self.assertEqual(302, result["http_status"])
        self.assertTrue(Path(str(path) + ".sending.json").exists())

    def test_201_wrong_shape_is_delivery_unknown(self):
        server = self._server(lambda h: (201, {
            "ok": True, "kind": "issue", "number": "12",
            "url": "http://not-https"}))
        path, sha, _ = self._draft_file()
        result = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", result["status"])
        self.assertEqual(201, result["http_status"])
        self.assertFalse(Path(str(path) + ".receipt.json").exists())
        self.assertTrue(Path(str(path) + ".sending.json").exists())

    def test_network_error_is_delivery_unknown(self):
        self._set_test_env("http://127.0.0.1:1")
        path, sha, _ = self._draft_file()
        result = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", result["status"])
        self.assertTrue(Path(str(path) + ".sending.json").exists())

    def test_lost_response_then_retry_unknown(self):
        state = {"drop": True}

        def responder(handler):
            if state["drop"]:
                return "drop"
            return 201, {"ok": True, "kind": "private"}

        server = self._server(responder)
        path, sha, _ = self._draft_file(kind="private")
        first = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", first["status"])
        self.assertEqual("outcome_unknown", first["reason"])
        self.assertEqual(1, len(server.requests))
        # claim이 남아 있으면 자동 재전송 없이 멈춘다 — POST 0회 증가.
        second = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", second["status"])
        self.assertEqual("previous_attempt_unconfirmed", second["reason"])
        self.assertEqual(1, len(server.requests))
        # --retry-unknown: claim을 .stale로 보존하고 다시 보낸다.
        state["drop"] = False
        code, third = self._cli(
            ["send", "--draft", str(path), "--confirm", sha,
             "--retry-unknown"])
        self.assertEqual(0, code, third)
        self.assertEqual("sent", third["status"])
        self.assertEqual(2, len(server.requests))
        self.assertTrue(self._sending(path, ".sending.*.stale.json"))
        self.assertTrue(self._sending(path, ".sending.*.done.json"))

    def test_receipt_write_failure_sent_unrecorded(self):
        server = self._server(lambda h: (201, {
            "ok": True, "kind": "issue", "number": 5,
            "url": "https://github.com/x/5"}))
        path, sha, _ = self._draft_file()
        # 영수증 경로를 디렉터리로 막아 쓰기 실패를 재현한다.
        Path(str(path) + ".receipt.json").mkdir()
        result = self.gm.send(path, sha)
        self.assertEqual("sent_unrecorded", result["status"])
        self.assertEqual(5, result["number"])
        self.assertEqual(1, len(server.requests))
        # claim은 남아 다음 send는 delivery_unknown으로 멈춘다.
        again = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", again["status"])
        self.assertEqual(1, len(server.requests))

    def test_receipt_conflict(self):
        path, sha, _ = self._draft_file()
        receipt = {"schema": self.gm.RECEIPT_SCHEMA,
                   "draft_sha256": "0" * 64, "kind": "issue",
                   "number": 1, "url": "https://github.com/x/1",
                   "sent_at": "2026-09-27T00:00:00Z", "test_mode": False}
        Path(str(path) + ".receipt.json").write_text(
            json.dumps(receipt), encoding="utf-8")
        result = self.gm.send(path, sha)
        self.assertEqual("receipt_conflict", result["error"])

    def test_receipt_invalid_shape(self):
        path, sha, _ = self._draft_file()
        receipt = {"schema": self.gm.RECEIPT_SCHEMA,
                   "draft_sha256": sha, "kind": "issue"}
        Path(str(path) + ".receipt.json").write_text(
            json.dumps(receipt), encoding="utf-8")
        result = self.gm.send(path, sha)
        self.assertEqual("receipt_invalid", result["error"])

    def test_existing_claim_blocks_second_send(self):
        server = self._server(
            lambda h: (201, {"ok": True, "kind": "private"}))
        path, sha, _ = self._draft_file(kind="private")
        claim = {"schema": self.gm.CLAIM_SCHEMA, "draft_sha256": sha,
                 "started_at": "2026-09-27T00:00:00Z", "token": "abc"}
        Path(str(path) + ".sending.json").write_text(
            json.dumps(claim), encoding="utf-8")
        result = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", result["status"])
        self.assertEqual(0, len(server.requests))

    def test_race_a_then_b_exactly_one_post(self):
        """r3 R2-02 회귀: B가 영수증 검사를 통과한 뒤 A가 전송을 끝내면
        B는 claim 획득 뒤 재확인에서 already_sent, 전체 POST 1회."""
        server = self._server(lambda h: (201, {
            "ok": True, "kind": "issue", "number": 9,
            "url": "https://github.com/x/9"}))
        path, sha, _ = self._draft_file()
        result = self.gm.send(
            path, sha,
            _pre_claim_hook=lambda: self.gm.send(path, sha))
        self.assertEqual("already_sent", result["status"])
        self.assertEqual(1, len(server.requests))
        self.assertTrue(self._sending(path, ".sending.*.dup.json"))
        self.assertTrue(self._sending(path, ".sending.*.done.json"))
        self.assertTrue(Path(str(path) + ".receipt.json").is_file())

    def test_done_record_without_receipt_is_already_sent(self):
        server = self._server(lambda h: (201, {
            "ok": True, "kind": "issue", "number": 2,
            "url": "https://github.com/x/2"}))
        path, sha, _ = self._draft_file()
        done = {"schema": self.gm.CLAIM_SCHEMA, "draft_sha256": sha,
                "started_at": "2026-09-27T00:00:00Z", "token": "abcd",
                "test_mode": True}
        Path(str(path) + ".sending.20260927T000000Z.done.json").write_text(
            json.dumps(done), encoding="utf-8")
        result = self.gm.send(path, sha)
        self.assertEqual("already_sent", result["status"])
        self.assertEqual(0, len(server.requests))

    def test_other_sha_done_record_is_ignored_f1_04(self):
        # F1-04: 다른 초안(sha)의 완전한 done은 이 초안의 성공 증거가 아니다.
        server = self._server(lambda h: (201, {
            "ok": True, "kind": "issue", "number": 3,
            "url": "https://github.com/x/3"}))
        path, sha, _ = self._draft_file()
        Path(str(path) + ".sending.b.done.json").write_text(json.dumps(
            {"schema": self.gm.CLAIM_SCHEMA, "draft_sha256": "0" * 64,
             "started_at": "x", "token": "t"}), encoding="utf-8")
        result = self.gm.send(path, sha)
        self.assertEqual("sent", result["status"])
        self.assertEqual(1, len(server.requests))

    def test_unverifiable_done_record_blocks_default_send_f1_r2_02(self):
        # F1 R2-02: 실제 첫 성공 → 영수증 부재 → done 손상 → 기본 재호출은
        # 요청 증가 0(delivery_unknown). --retry-unknown만 새 요청을 만든다.
        server = self._server(lambda h: (201, {
            "ok": True, "kind": "issue", "number": 5,
            "url": "https://github.com/x/5"}))
        for damage in ("{", "{}", "no-token"):
            with self.subTest(damage=damage):
                before = len(server.requests)
                path, sha, _ = self._draft_file(name="d%d" % len(damage))
                self.assertEqual("sent", self.gm.send(path, sha)["status"])
                receipt = Path(str(path) + ".receipt.json")
                receipt.rename(Path(str(path) + ".receipt.moved"))
                done = self._sending(path, ".sending.*.done.json")[0]
                if damage == "no-token":
                    doc = json.loads(done.read_text(encoding="utf-8"))
                    doc.pop("token")
                    done.write_text(json.dumps(doc), encoding="utf-8")
                else:
                    done.write_text(damage, encoding="utf-8")
                again = self.gm.send(path, sha)
                self.assertEqual("delivery_unknown", again["status"])
                self.assertEqual("unverifiable_done_record", again["reason"])
                self.assertEqual(before + 1, len(server.requests))
                self.assertTrue(done.exists())
                retried = self.gm.send(path, sha, retry_unknown=True)
                self.assertEqual("sent", retried["status"])
                self.assertEqual(before + 2, len(server.requests))

    def test_non_string_error_is_delivery_unknown_cli_f1_r2_01(self):
        # F1 R2-01: error가 문자열이 아니면 계약 밖 — 예외 없이 JSON 한 개,
        # exit 1, claim 유지, 기본 재호출 요청 증가 0.
        for error in ([], {}, None, 5):
            with self.subTest(error=error):
                server = self._server(
                    lambda h, e=error: (502, {"ok": False, "error": e}))
                path, sha, _ = self._draft_file(name="e%s" % type(error).__name__)
                code, result = self._cli(
                    ["send", "--draft", str(path), "--confirm", sha])
                self.assertEqual(1, code)
                self.assertEqual("delivery_unknown", result["status"])
                self.assertTrue(Path(str(path) + ".sending.json").exists())
                code, result = self._cli(
                    ["send", "--draft", str(path), "--confirm", sha])
                self.assertEqual("delivery_unknown", result["status"])
                self.assertEqual(1, len(server.requests))
        server = self._server(lambda h: (502, {
            "ok": False, "error": "github_failed", "status": 500}))
        path, sha, _ = self._draft_file(name="estr")
        code, result = self._cli(
            ["send", "--draft", str(path), "--confirm", sha])
        self.assertEqual(1, code)
        self.assertEqual("not_sent", result["status"])
        self.assertFalse(Path(str(path) + ".sending.json").exists())

    def test_uncontracted_502_is_delivery_unknown_f1_03(self):
        # F1-03: 계약 밖 502(HTML 게이트웨이 오류)는 접수 불명 — claim 유지,
        # 기본 재전송은 요청 0회, --retry-unknown만 새 요청을 만든다.
        state = {"html": True}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                outer._requests.append(1)
                if state["html"]:
                    data = b"<html>upstream response unavailable</html>"
                    self.send_response(502)
                    self.send_header("Content-Type", "text/html")
                else:
                    data = json.dumps({"ok": True, "kind": "issue",
                                       "number": 4,
                                       "url": "https://github.com/x/4"}
                                      ).encode("utf-8")
                    self.send_response(201)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._requests = []
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self._set_test_env("http://127.0.0.1:%d" % httpd.server_address[1])
        path, sha, _ = self._draft_file()
        first = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", first["status"])
        self.assertEqual(502, first["http_status"])
        self.assertTrue(Path(str(path) + ".sending.json").exists())
        second = self.gm.send(path, sha)
        self.assertEqual("delivery_unknown", second["status"])
        self.assertEqual(1, len(self._requests))
        state["html"] = False
        third = self.gm.send(path, sha, retry_unknown=True)
        self.assertEqual("sent", third["status"])
        self.assertEqual(2, len(self._requests))
        self.assertTrue(self._sending(path, ".sending.*.stale.json"))

    def test_mismatched_error_code_is_delivery_unknown_f1_03(self):
        # issue 전송에 discord_failed(다른 kind의 코드)는 계약 밖.
        self._server(lambda h: (502, {"ok": False,
                                      "error": "discord_failed"}))
        path, sha, _ = self._draft_file()
        self.assertEqual("delivery_unknown",
                         self.gm.send(path, sha)["status"])
        self.assertTrue(Path(str(path) + ".sending.json").exists())

    def test_cli_send_exit_codes(self):
        server = self._server(
            lambda h: (429, {"ok": False, "error": "rate_limited"}))
        path, sha, _ = self._draft_file()
        code, result = self._cli(
            ["send", "--draft", str(path), "--confirm", sha])
        self.assertEqual(1, code)
        self.assertEqual("not_sent", result["status"])


class DocsTests(_ReportCase):
    def test_skill_first_entry_notice(self):
        text = (REPO_ROOT / "skills/knuaf-doc/SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn(
            "쓰다가 오류나 불편한 점이 있으면 '신고할래'라고 말하면 "
            "개발자에게 전달됩니다", text)

    def test_skill_report_section_and_link(self):
        text = (REPO_ROOT / "skills/knuaf-doc/SKILL.md").read_text(
            encoding="utf-8")
        report_at = text.index("## 오류·불편 신고")
        limit_at = text.index("## 출력 지원 한계")
        self.assertLess(report_at, limit_at)
        self.assertIn("[student-report](references/student-report.md)",
                      text)
        # r2 D1 추가: 원본 비외송 원칙의 한정 예외 명시.
        self.assertIn("원본 파일·원고·정본은 보내지 않습니다", text)

    def test_reference_doc_exists_for_ai(self):
        doc = (REPO_ROOT / "skills/knuaf-doc/references/student-report.md")
        text = doc.read_text(encoding="utf-8")
        self.assertIn("gg_report.py", text)
        self.assertIn("scripts/gg_report.py", text)

    def test_readme_install_notice(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("신고할래", text)
        self.assertIn("개인 정보는 공개 이슈에 올리지 않습니다", text)


if __name__ == "__main__":
    unittest.main()
