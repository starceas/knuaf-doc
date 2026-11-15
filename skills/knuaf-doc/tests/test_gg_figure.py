"""gg_figure — 앱 통로·직접 받기·place 계약 시험 (외부 사이트 접속 없음)."""

import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))
import gg_document  # noqa: E402
import gg_figure  # noqa: E402

HOST = "www.nongsaro.go.kr"
IMAGE02 = "https://www.nongsaro.go.kr/ps/img/curation/20240209/images/image02.jpg"
IMAGE12 = "https://www.nongsaro.go.kr/ps/img/curation/20240209/images/image12.jpg"
DETAIL_PATH = "/portal/ps/psv/psvr/psvre/curationDtl.ps?menuId=PS03352&srchCurationNo=1941"
PAGE_URL = "https://" + HOST + DETAIL_PATH


def fixture(name):
    return (FIXTURES / name).read_bytes()


def https_page(name):
    """시험용 페이지: 실제 페이지의 http 절대 URL만 https로 바꾼다."""
    text = fixture(name).decode("utf-8", "replace")
    return text.replace("http://" + HOST, "https://" + HOST)


def rule(page="^/portal/ps/psv/psvr/psvre/curationDtl\\.ps$",
         image="^/ps/img/curation/.+"):
    return {"id": "test-rule", "host": HOST, "org": "농촌진흥청",
            "pagePath": re.compile(page), "imagePath": re.compile(image),
            "rule": "page_kogl_applies_to_body_images"}


def gif_bytes(frames=1, width=10, height=10):
    out = bytearray(b"GIF89a")
    out += width.to_bytes(2, "little") + height.to_bytes(2, "little")
    out += bytes([0x00, 0x00, 0x00])
    for _ in range(frames):
        out += b"\x2c" + bytes(9) + b"\x08" + b"\x00"
    out += b"\x3b"
    return bytes(out)


def png_header(width, height):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
            + width.to_bytes(4, "big") + height.to_bytes(4, "big"))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        self.server.seen[self.path] = {
            key.lower(): value for key, value in self.headers.items()}
        host = urllib.parse.urlsplit("//" + self.headers["host"]).hostname
        self.server.hits.append((host, self.path))
        route = self.server.routes.get(self.path)
        if route is None:
            self.send_error(404)
            return
        status, headers, body = route
        if callable(body):          # 조금씩 흘리는 서버(R-1) 시험용
            body(self)
            return
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestVersion(unittest.TestCase):
    """판 문자열은 ``version.json`` 하나에서 온다 — 상수로 박지 않는다."""

    def test_version_matches_the_deployed_version_file(self):
        doc = json.loads((SCRIPTS.parent / "version.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(doc["version"], gg_figure.VERSION)

    def test_unreadable_version_file_falls_back_to_unknown(self):
        """판독 불가는 그림 기능을 막지 않는다 — UA 판만 ``unknown``이 된다."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "version.json"
            for text in ("{}", '{"schema": "knuaf-doc/version@1"}',
                         '{"schema": "wrong", "version": "1.2.3", '
                         '"repo": "starceas/knuaf-doc"}',
                         '{"schema": "knuaf-doc/version@1", "version": "1.2", '
                         '"repo": "starceas/knuaf-doc"}',
                         "not json"):
                path.write_text(text, encoding="utf-8")
                with self.subTest(text=text):
                    with mock.patch.object(gg_figure, "VERSION_FILE", path):
                        self.assertEqual("unknown", gg_figure.skill_version())
            absent = Path(temp) / "absent.json"
            with mock.patch.object(gg_figure, "VERSION_FILE", absent):
                self.assertEqual("unknown", gg_figure.skill_version())


class TestMarks(unittest.TestCase):
    def test_detail_fixture_single_type(self):
        types, marks = gg_figure.kogl_marks(https_page("nongsaro-1941-detail.html"))
        self.assertEqual(types, {2})
        self.assertEqual(len(marks), 3)

    def test_license_fixture_single_type(self):
        types, _ = gg_figure.kogl_marks(https_page("nongsaro-1941-license.html"))
        self.assertEqual(types, {2})

    def test_script_body_ignored_comment_kept(self):
        html = ('<script>var a = "open_mark4.png 제4유형";</script>'
                '<!-- <img src="/x/open_mark1.png"> -->')
        types, _ = gg_figure.kogl_marks(html)
        self.assertEqual(types, {1})

    def test_icon_open_n_is_not_type_n(self):
        html = '<img src="/ps/img/footer/icon_open1.gif" title="제2유형">'
        types, _ = gg_figure.kogl_marks(html)
        self.assertEqual(types, {2})


class TestUrls(unittest.TestCase):
    def test_accepts_canonical(self):
        self.assertEqual(gg_figure.normalize_url(IMAGE02, host=HOST,
                                                 path_pattern=re.compile(r"^/ps/img/curation/.+")),
                         IMAGE02)

    def test_accepts_idn_and_trailing_dot(self):
        self.assertEqual(
            gg_figure.normalize_url("https://www.nongsaro.go.kr./a", host=HOST),
            "https://www.nongsaro.go.kr/a")
        self.assertEqual(
            gg_figure.normalize_url("https://www.nongsaro.go.kr/a?b=1", host=HOST),
            "https://www.nongsaro.go.kr/a?b=1")

    def test_rejects(self):
        cases = {
            "http://www.nongsaro.go.kr/a": "not_https",
            "https://u:p@www.nongsaro.go.kr/a": "userinfo",
            "https://1.2.3.4/a": "ip_literal",
            "https://www.nongsaro.go.kr:8443/a": "bad_port",
            "https://other.example/a": "host_not_allowed",
            "https://www.nongsaro.go.kr/elsewhere": "path_not_allowed",
        }
        for url, reason in cases.items():
            with self.subTest(url=url):
                with self.assertRaises(gg_figure.FigureError) as caught:
                    gg_figure.normalize_url(
                        url, host=HOST, path_pattern=re.compile(r"^/ps/img/curation/.+"))
                self.assertEqual(caught.exception.reason, reason)

    def test_bad_address(self):
        bad = ["10.0.0.1", "127.0.0.1", "169.254.1.1", "100.64.0.1",
               "::ffff:10.0.0.1", "fd00::1", "0.0.0.0", "::1", "224.0.0.1",
               "not-an-ip"]
        for addr in bad:
            with self.subTest(addr=addr):
                self.assertTrue(gg_figure._bad_address(addr))
        for addr in ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"]:
            with self.subTest(addr=addr):
                self.assertFalse(gg_figure._bad_address(addr))

    def test_resolve_addresses_fail_closed(self):
        mixed = lambda host, port: ["8.8.8.8", "10.0.0.1"]  # noqa: E731
        with self.assertRaises(gg_figure.FigureError) as caught:
            gg_figure.resolve_addresses(HOST, mixed)
        self.assertEqual(caught.exception.reason, "private_address")

        loop = lambda host, port: ["127.0.0.1"]  # noqa: E731
        with self.assertRaises(gg_figure.FigureError):
            gg_figure.resolve_addresses(HOST, loop)
        self.assertEqual(gg_figure.resolve_addresses(HOST, loop, True), "127.0.0.1")

        private = lambda host, port: ["10.0.0.1"]  # noqa: E731
        with self.assertRaises(gg_figure.FigureError):
            gg_figure.resolve_addresses(HOST, private, allow_loopback=True)


class _ServerCase(unittest.TestCase):
    """127.0.0.1 HTTPS 서버. 443 고정 대신 포트만 시험용으로 바꾼다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gg-figure-"))
        key = cls.tmp / "key.pem"
        cert = cls.tmp / "cert.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(cert), "-days", "1",
             "-subj", "/CN=" + HOST,
             "-addext", "subjectAltName=DNS:" + HOST + ",DNS:www.example.go.kr"
                        ",DNS:plain.example.go.kr"],
            check=True, capture_output=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.server.routes = {}
        cls.server.seen = {}
        cls.server.hits = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.tls = ssl.create_default_context(cafile=str(cert))
        cls._original = gg_figure._PinnedHTTPSConnection
        port = cls.port

        class _TestConnection(cls._original):
            def __init__(self, host, address, port_=port, timeout=30.0,
                         context=None):
                super().__init__(host, address, port=port, timeout=timeout,
                                 context=context)

        gg_figure._PinnedHTTPSConnection = _TestConnection

    @classmethod
    def tearDownClass(cls):
        gg_figure._PinnedHTTPSConnection = cls._original
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def route(self, path, body=b"", status=200, headers=None):
        self.server.routes[path] = (status, headers or {}, body)

    def resolver(self, host, port):
        return ["127.0.0.1"]

    def get(self, url, kind="page", rules=None, limiter=None):
        return gg_figure.http_get(
            url, rule=rules or rule(), kind=kind, resolver=self.resolver,
            allow_loopback=True, tls_context=self.tls, limiter=limiter)


class TestHttp(_ServerCase):
    def setUp(self):
        self.server.routes.clear()
        self.server.seen.clear()

    def test_user_agent_carries_the_deployed_version(self):
        """UA의 판은 ``version.json``에서 온다 — 상수로 박으면 판마다 손대야 한다."""
        self.route(DETAIL_PATH, b"ok")
        self.get("https://" + HOST + DETAIL_PATH)
        doc = json.loads((SCRIPTS.parent / "version.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual("knuaf-doc/" + doc["version"],
                         self.server.seen[DETAIL_PATH]["user-agent"])

    def test_fetches_page_and_image(self):
        self.route(DETAIL_PATH, https_page("nongsaro-1941-license.html").encode())
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"), headers={"content-type": "image/jpeg"})
        final, status, _, body = self.get("https://" + HOST + DETAIL_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(final, "https://" + HOST + DETAIL_PATH)
        self.assertEqual(body, https_page("nongsaro-1941-license.html").encode())
        _, _, _, image = self.get(IMAGE02, kind="image")
        self.assertEqual(image, fixture("nongsaro-image02.jpg"))

    def test_redirect_chain_within_limit(self):
        self.route("/p/r0", status=302, headers={"location": "/p/r1"})
        self.route("/p/r1", status=302, headers={"location": "/p/r2"})
        self.route("/p/r2", status=302, headers={"location": "/p/r3"})
        self.route("/p/r3", b"ok")
        final, status, _, body = self.get(
            "https://" + HOST + "/p/r0", rules=rule("^/p/.+", "^/img/.+"))
        self.assertEqual((final, status, body),
                         ("https://" + HOST + "/p/r3", 200, b"ok"))

    def test_too_many_redirects(self):
        for index in range(5):
            self.route("/p/r%d" % index, status=302,
                       headers={"location": "/p/r%d" % (index + 1)})
        self.route("/p/r5", b"ok")
        with self.assertRaises(gg_figure.FigureError) as caught:
            self.get("https://" + HOST + "/p/r0", rules=rule("^/p/.+", "^/img/.+"))
        self.assertEqual(caught.exception.reason, "too_many_redirects")

    def test_redirect_host_and_scheme_rechecked(self):
        self.route("/p/x", status=302,
                   headers={"location": "https://evil.example/p/x"})
        with self.assertRaises(gg_figure.FigureError) as caught:
            self.get("https://" + HOST + "/p/x", rules=rule("^/p/.+", "^/img/.+"))
        self.assertEqual(caught.exception.reason, "host_not_allowed")

        self.route("/p/y", status=302,
                   headers={"location": "http://" + HOST + "/p/z"})
        with self.assertRaises(gg_figure.FigureError) as caught:
            self.get("https://" + HOST + "/p/y", rules=rule("^/p/.+", "^/img/.+"))
        self.assertEqual(caught.exception.reason, "not_https")

    def test_http_status(self):
        with self.assertRaises(gg_figure.FigureError) as caught:
            self.get("https://" + HOST + "/p/missing",
                     rules=rule("^/p/.+", "^/img/.+"))
        self.assertEqual(caught.exception.reason, "http_status")

    def test_page_cap(self):
        self.route("/p/big", b"x" * (gg_figure.MAX_HTML_BYTES + 1))
        with self.assertRaises(gg_figure.FigureError) as caught:
            self.get("https://" + HOST + "/p/big", rules=rule("^/p/.+", "^/img/.+"))
        self.assertEqual(caught.exception.reason, "too_large")

    def test_image_cap(self):
        self.route("/img/big.jpg", b"\xff\xd8" + b"0" * gg_figure.MAX_IMAGE_BYTES)
        with self.assertRaises(gg_figure.FigureError) as caught:
            self.get("https://" + HOST + "/img/big.jpg", kind="image",
                     rules=rule("^/p/.+", "^/img/.+"))
        self.assertEqual(caught.exception.reason, "too_large")

    def test_request_total_cap(self):
        self.route("/img/small.jpg", fixture("nongsaro-image02.jpg"))
        limiter = {"bytes": gg_figure.MAX_REQUEST_BYTES - 10, "deadline": None}
        with self.assertRaises(gg_figure.FigureError) as caught:
            self.get("https://" + HOST + "/img/small.jpg", kind="image",
                     rules=rule("^/p/.+", "^/img/.+"), limiter=limiter)
        self.assertEqual(caught.exception.reason, "too_large")

    def test_deadline(self):
        self.route("/p/ok", b"ok")
        limiter = {"bytes": 0, "deadline": 0.0}
        with self.assertRaises(gg_figure.FigureError) as caught:
            self.get("https://" + HOST + "/p/ok", rules=rule("^/p/.+", "^/img/.+"),
                     limiter=limiter)
        self.assertEqual(caught.exception.reason, "timeout")


class TestImageChecks(_ServerCase):
    def test_sizes(self):
        self.assertEqual(gg_figure._image_size(fixture("nongsaro-image02.jpg")),
                         ("jpeg", 1176, 316, 1))
        self.assertEqual(gg_figure._image_size(fixture("nongsaro-image12.jpg")),
                         ("jpeg", 1176, 692, 1))
        self.assertEqual(gg_figure._image_size(png_header(30, 40)),
                         ("png", 30, 40, 1))
        self.assertEqual(gg_figure._image_size(gif_bytes(1)), ("gif", 10, 10, 1))
        self.assertEqual(gg_figure._image_size(gif_bytes(3))[3], 3)

    def test_bad_image(self):
        with self.assertRaises(gg_figure.FigureError) as caught:
            gg_figure._image_size(b"not an image")
        self.assertEqual(caught.exception.reason, "bad_image")

    def test_page_references(self):
        html = https_page("nongsaro-1941-license.html")
        self.assertIsNotNone(gg_figure._page_references(html, IMAGE02, PAGE_URL))
        self.assertIsNone(gg_figure._page_references(
            html, "https://" + HOST + "/ps/img/curation/20240209/images/none.jpg",
            PAGE_URL))

    def test_page_references_http_src_matches_https_image(self):
        """CONTRACT §8: 페이지의 http:// 표기를 같은 그림으로 본다(받기는 https)."""
        html = fixture("nongsaro-1941-license.html").decode("utf-8", "replace")
        self.assertIn('http://' + HOST, html)
        self.assertIsNotNone(gg_figure._page_references(html, IMAGE02, PAGE_URL))

    def test_http_equivalence_requires_same_host_path_query(self):
        html = ('<img src="http://%s/ps/img/curation/20240209/images/image02.jpg">'
                % HOST)
        self.assertIsNotNone(gg_figure._page_references(html, IMAGE02, PAGE_URL))
        # 다른 호스트
        self.assertIsNone(gg_figure._page_references(
            html, IMAGE02.replace(HOST, "www.example.com"), PAGE_URL))
        # 다른 쿼리
        self.assertIsNone(gg_figure._page_references(html, IMAGE02 + "?v=2", PAGE_URL))
        # 다른 경로
        self.assertIsNone(gg_figure._page_references(
            html, IMAGE02.replace("image02", "image03"), PAGE_URL))

    def test_http_equivalence_rejects_non_default_port(self):
        html = '<img src="http://%s:8080/ps/img/curation/20240209/images/image02.jpg">' % HOST
        self.assertIsNone(gg_figure._page_references(html, IMAGE02, PAGE_URL))
        explicit = '<img src="https://%s:443/ps/img/curation/20240209/images/image02.jpg">' % HOST
        self.assertIsNotNone(gg_figure._page_references(explicit, IMAGE02, PAGE_URL))

    def test_http_equivalence_rejects_port_of_other_scheme(self):
        """B-1: 스킴 기본 포트만 같게 본다 — http:443·https:80은 다른 자원이다."""
        base = "/ps/img/curation/20240209/images/image02.jpg"
        for src in ("http://%s:443" % HOST, "https://%s:80" % HOST):
            with self.subTest(src=src):
                html = '<img src="%s%s">' % (src, base)
                self.assertIsNone(
                    gg_figure._page_references(html, IMAGE02, PAGE_URL))
        for src in ("http://%s" % HOST, "https://%s" % HOST,
                    "https://%s:443" % HOST, "http://%s:80" % HOST):
            with self.subTest(src=src):
                html = '<img src="%s%s">' % (src, base)
                self.assertIsNotNone(
                    gg_figure._page_references(html, IMAGE02, PAGE_URL))

    def test_third_party_window_spans_the_whole_anchor_tag(self):
        """B-2: 긴 속성이 붙은 <a>도 태그 끝 기준으로 창을 잡는다."""
        pad = ' data-note="' + "x" * 400 + '"'
        url = "https://%s/ps/img/curation/b.jpg" % HOST
        for markup in ('<a href="%s"%s>원본</a>' % (url, pad),
                       '<img src="%s"%s>' % (url, pad)):
            with self.subTest(tag=markup[:2]):
                html = markup + "<span>사진 제공: 홍길동</span>"
                anchor = gg_figure._page_references(html, url, PAGE_URL)
                self.assertIsNotNone(anchor)
                self.assertEqual(
                    gg_figure._third_party_near(html, anchor), "사진 제공")

    def test_third_party_window(self):
        def near(html, url=IMAGE02):
            return gg_figure._third_party_near(
                html, gg_figure._page_references(html, url, PAGE_URL))

        html = ('<figure><img src="%s"><figcaption>사진 제공: 김철수</figcaption></figure>'
                % IMAGE02)
        self.assertEqual(near(html), "사진 제공")
        clean = ('<figure><img src="%s"><figcaption>출처표시 조건</figcaption></figure>'
                 % IMAGE02)
        self.assertIsNone(near(clean))
        credit = ('<figure><img src="%s"><figcaption>출처 : 농촌진흥청</figcaption></figure>'
                  % IMAGE02)
        self.assertEqual(near(credit), "출처 :")

    def test_third_party_window_anchors_on_anchor_href_only_image(self):
        """B-2: 그림이 <a href>로만 걸려도 그 자리의 '사진 제공'을 본다."""
        html = ('<p>open_mark2.png</p>'
                '<a href="https://%s/ps/img/curation/b.jpg">원본</a>'
                ' <span>사진 제공: 홍길동</span>' % HOST)
        url = "https://%s/ps/img/curation/b.jpg" % HOST
        anchor = gg_figure._page_references(html, url, PAGE_URL)
        self.assertIsNotNone(anchor)
        self.assertEqual(gg_figure._third_party_near(html, anchor), "사진 제공")

    def test_relative_src_resolves_against_page_not_image(self):
        """I-1: 상대 src는 페이지 최종 URL 기준으로 푼다."""
        html = '<img src="c.jpg">'
        page = "https://%s/ps/img/curation/20240209/" % HOST
        self.assertIsNotNone(gg_figure._page_references(
            html, "https://%s/ps/img/curation/20240209/c.jpg" % HOST, page))
        self.assertIsNone(gg_figure._page_references(
            html, "https://%s/ps/img/curation/c.jpg" % HOST, page))

    def test_percent_encoded_src_matches_raw_image_url(self):
        """I-2: 페이지의 퍼센트 표기와 인코딩 안 된 그림 URL이 같은 키가 된다."""
        html = '<img src="/ps/img/curation/%ED%95%9C%EA%B8%80.jpg">'
        raw = "https://%s/ps/img/curation/한글.jpg" % HOST
        self.assertIsNotNone(gg_figure._page_references(html, raw, PAGE_URL))


class TestFetchDirect(_ServerCase):
    def setUp(self):
        self.server.routes.clear()
        self.folder = Path(tempfile.mkdtemp(prefix="gg-folder-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.route(DETAIL_PATH, https_page("nongsaro-1941-license.html").encode())
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        self.route("/ps/img/curation/20240209/images/image12.jpg",
                   fixture("nongsaro-image12.jpg"))

    def fetch(self, page, images, slot="s1", rules=None):
        return gg_figure.fetch_direct(
            self.folder, page, images, slot,
            rules=[rule()] if rules is None else rules,
            resolver=self.resolver, allow_loopback=True,
            tls_context=self.tls)

    def test_single_approved(self):
        result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["mode"], "direct")
        self.assertFalse(result["needsChoice"])
        self.assertEqual(result["page"]["kogl"], 2)
        self.assertEqual(result["page"]["ruleId"], "test-rule")
        image = result["images"][0]
        self.assertEqual(image["verdict"], "approved")
        self.assertEqual(image["sha256"],
                         "d3ced52a43e124d79e94e987f6cad35fc966fb9249c858c57f89e2f820c34162")
        stored = self.folder / image["path"]
        self.assertTrue(stored.is_file())
        self.assertEqual(gg_figure.digest_file(stored), image["sha256"])

    def test_two_approved_needs_choice_with_labels(self):
        result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02, IMAGE12])
        self.assertTrue(result["needsChoice"])
        labels = [item["label"] for item in result["images"]]
        self.assertEqual(labels, ["A", "B"])

    def test_http_page_src_still_approves_https_image(self):
        """CONTRACT §8 전 구간: 페이지가 http://로 적어도 받기는 https로 해석된다."""
        self.route(DETAIL_PATH, fixture("nongsaro-1941-license.html"))
        result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["images"][0]["verdict"], "approved")
        self.assertTrue(result["images"][0]["finalUrl"].startswith("https://"))

    def test_http_page_src_with_other_host_stays_ambiguous(self):
        page = fixture("nongsaro-1941-license.html").decode("utf-8", "replace").replace(
            'http://' + HOST + '/ps/img/curation/', 'http://images.example.com/x/').encode()
        self.route(DETAIL_PATH, page)
        result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02])
        self.assertEqual(result["images"][0]["verdict"], "ambiguous")
        self.assertEqual(result["images"][0]["reason"], "not_on_page")

    def test_no_rule(self):
        result = gg_figure.fetch_direct(
            self.folder, "https://" + HOST + "/ps/img/curation/x.jpg",
            [IMAGE02], "s1", rules=[rule()])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "no_rule")

    def test_rule_absent(self):
        result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02], rules=[])
        self.assertEqual(result["reason"], "no_rules")

    def test_kogl_three_and_four_ambiguous(self):
        for kind in (3, 4):
            with self.subTest(kind=kind):
                self.route(DETAIL_PATH,
                           ('<p>제%d유형</p><img src="%s">' % (kind, IMAGE02)).encode())
                result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02])
                self.assertEqual(result["images"][0]["verdict"], "ambiguous")
                self.assertEqual(result["images"][0]["reason"], "kogl_%d" % kind)

    def test_kogl_multi_ambiguous(self):
        self.route(DETAIL_PATH,
                   ('<p>open_mark1.png</p><p>open_mark3.png</p><img src="%s">'
                    % IMAGE02).encode())
        result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02])
        self.assertEqual(result["images"][0]["reason"], "kogl_ambiguous")

    def test_not_on_page(self):
        self.route(DETAIL_PATH, b"<p>open_mark2.png</p>")
        result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02])
        self.assertEqual(result["images"][0]["verdict"], "ambiguous")
        self.assertEqual(result["images"][0]["reason"], "not_on_page")

    def test_third_party_ambiguous(self):
        page = ('<p>open_mark2.png</p><figure><img src="%s">'
                '<figcaption>사진 제공: 김철수</figcaption></figure>' % IMAGE02)
        self.route(DETAIL_PATH, page.encode())
        result = self.fetch("https://" + HOST + DETAIL_PATH, [IMAGE02])
        self.assertEqual(result["images"][0]["reason"], "third_party_mark:사진 제공")

    def test_image_url_outside_rule(self):
        result = self.fetch("https://" + HOST + DETAIL_PATH,
                            ["https://" + HOST + "/ps/img/other/x.jpg"])
        self.assertEqual(result["images"][0]["verdict"], "rejected")
        self.assertEqual(result["images"][0]["reason"], "path_not_allowed")

    def test_animated_gif_rejected(self):
        self.route("/ps/img/curation/a.gif", gif_bytes(2))
        page = ('<p>open_mark2.png</p><img src="https://%s/ps/img/curation/a.gif">'
                % HOST)
        self.route(DETAIL_PATH, page.encode())
        result = self.fetch("https://" + HOST + DETAIL_PATH,
                            ["https://" + HOST + "/ps/img/curation/a.gif"])
        self.assertEqual(result["images"][0]["verdict"], "rejected")
        self.assertEqual(result["images"][0]["reason"], "animated")

    def test_too_many_pixels_rejected(self):
        self.route("/ps/img/curation/huge.png", png_header(9000, 9000))
        page = ('<p>open_mark2.png</p><img src="https://%s/ps/img/curation/huge.png">'
                % HOST)
        self.route(DETAIL_PATH, page.encode())
        result = self.fetch("https://" + HOST + DETAIL_PATH,
                            ["https://" + HOST + "/ps/img/curation/huge.png"])
        self.assertEqual(result["images"][0]["reason"], "too_many_pixels")


class TestChannel(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gg-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.folder = Path(tempfile.mkdtemp(prefix="gg-folder-"))
        self.addCleanup(shutil.rmtree, self.folder, True)

    def beat(self, offset_ms=0):
        now = int(__import__("time").time() * 1000) + offset_ms
        (self.home / "figure-channel.json").write_text(
            json.dumps({"v": 1, "pid": 1, "updatedAt": now}), encoding="utf-8")

    def turn(self, folder=None, started=None):
        payload = {"v": 1, "turnId": "turn-1",
                   "folder": str(folder or self.folder),
                   "startedAt": started if started is not None else 0}
        (self.home / "turn.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_channel_states(self):
        self.assertEqual(gg_figure.channel_state(self.home)[1], "no_heartbeat")
        self.beat()
        self.assertEqual(gg_figure.channel_state(self.home), (True, ""))
        self.beat(-20_000)
        self.assertEqual(gg_figure.channel_state(self.home)[1], "stale_heartbeat")
        self.beat(20_000)
        self.assertEqual(gg_figure.channel_state(self.home)[1], "stale_heartbeat")
        self.assertEqual(
            gg_figure.channel_state(self.home / "missing")[1], "no_channel")

    def test_turn_states(self):
        self.assertEqual(gg_figure.turn_state(self.home, self.folder)[1], "no_turn")
        self.turn(folder=self.home)
        self.assertEqual(gg_figure.turn_state(self.home, self.folder)[1],
                         "folder_mismatch")
        self.turn(started=2 ** 60)
        self.assertEqual(gg_figure.turn_state(self.home, self.folder)[1],
                         "invalid_turn")
        self.turn()
        self.assertEqual(gg_figure.turn_state(self.home, self.folder),
                         ("turn-1", ""))

    def test_payload_shape(self):
        payload = gg_figure.request_payload(
            "abcdefgh", "turn-1", "s1", "https://" + HOST + DETAIL_PATH, [IMAGE02])
        self.assertEqual(set(payload), {"v", "id", "turnId", "slot", "page", "images"})
        with self.assertRaises(gg_figure.FigureError):
            gg_figure.request_payload("short", "t", "s1", "https://" + HOST + "/a", [IMAGE02])
        with self.assertRaises(gg_figure.FigureError):
            gg_figure.request_payload("abcdefgh", "t", "s1", "http://" + HOST + "/a", [IMAGE02])
        with self.assertRaises(gg_figure.FigureError):
            gg_figure.request_payload("abcdefgh", "t", "s1", "https://" + HOST + "/a",
                                      [IMAGE02] * 4)

    def test_read_result_validation(self):
        res = self.home / "res"
        res.mkdir()
        (res / "abcdefgh.res.json").write_text(json.dumps(
            {"v": 1, "id": "abcdefgh", "status": "done", "slot": "s1",
             "images": [{"url": IMAGE02, "verdict": "approved", "sha256": "zz"}]}),
            encoding="utf-8")
        with self.assertRaises(gg_figure.FigureError) as caught:
            gg_figure.read_result(self.home, "abcdefgh")
        self.assertEqual(caught.exception.reason, "invalid_result")

    def test_fetch_app_writes_request_then_times_out(self):
        self.beat()
        self.turn()
        slept = []
        result = gg_figure.fetch_app(
            self.folder, "https://" + HOST + DETAIL_PATH, [IMAGE02], "s1",
            environ={"KNUAF_FIGURE_HOME": str(self.home)},
            sleep=lambda seconds: slept.append(seconds), poll_seconds=0.0)
        self.assertEqual(result["reason"], "timeout")
        requests = list((self.folder / ".knuaf-gui" / "figure-requests").glob("*.req.json"))
        self.assertEqual(len(requests), 1)
        payload = json.loads(requests[0].read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"v", "id", "turnId", "slot", "page", "images"})
        self.assertEqual(payload["turnId"], "turn-1")

    def test_fetch_app_stale_heartbeat_never_requests(self):
        self.beat(-20_000)
        self.turn()
        slept = []
        result = gg_figure.fetch_app(
            self.folder, "https://" + HOST + DETAIL_PATH, [IMAGE02], "s1",
            environ={"KNUAF_FIGURE_HOME": str(self.home)},
            sleep=lambda seconds: slept.append(seconds))
        self.assertEqual(result["reason"], "stale_heartbeat")
        self.assertEqual(slept, [])
        self.assertFalse((self.folder / ".knuaf-gui").exists())

    def test_fetch_app_reads_result(self):
        self.beat()
        self.turn()
        holder = {}

        def sleep(_seconds):
            req = next((self.folder / ".knuaf-gui" / "figure-requests").glob("*.req.json"))
            req_id = json.loads(req.read_text(encoding="utf-8"))["id"]
            res = self.home / "res"
            res.mkdir(exist_ok=True)
            (res / (req_id + ".res.json")).write_text(json.dumps({
                "v": 1, "id": req_id, "status": "done", "slot": "s1",
                "images": [{"url": IMAGE02, "verdict": "approved",
                            "sha256": "a" * 64}]}), encoding="utf-8")
            holder["id"] = req_id

        result = gg_figure.fetch_app(
            self.folder, "https://" + HOST + DETAIL_PATH, [IMAGE02], "s1",
            environ={"KNUAF_FIGURE_HOME": str(self.home)}, sleep=sleep)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["reqId"], holder["id"])

    def test_gui_mode_never_fetches_directly(self):
        env = {"KNUAF_GUI": "1", "KNUAF_FIGURE_HOME": str(self.home)}
        old = os.environ.copy()
        os.environ.update(env)
        try:
            with mock.patch("sys.stdout"):
                code = gg_figure.main(["fetch", str(self.folder), "--slot", "s1",
                                       "--page", "https://" + HOST + DETAIL_PATH,
                                       "--image", IMAGE02, "--direct"])
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(code, 2)
        self.assertFalse((self.folder / ".knuaf-gui").exists())
        self.assertFalse((self.folder / "figures").exists())


APP_ENV = {"KNUAF_GUI": "1"}

MANUSCRIPT = """# Ⅰ. 서론

## 가. 품종 선택

잎들깨 품종은 남천·동글2호·새봄이 있다.

## 나. 재배

수경재배로 재배한다.
"""


class TestPlace(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gg-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.folder = Path(tempfile.mkdtemp(prefix="gg-folder-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        (self.folder / "build").mkdir()
        self.manuscript = self.folder / "build" / "검토전_본문.md"
        self.manuscript.write_text(MANUSCRIPT, encoding="utf-8")

    def request_file(self, req_id="req-0001", slot="s1", mode="app"):
        path = self.folder / ".gg-work" / "figure-requests" / (slot + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": 1, "slot": slot, "reqId": req_id,
                                    "mode": mode, "at": 0}), encoding="utf-8")
        return path

    def result_file(self, payload, req_id="req-0001", mode="app"):
        """CONTRACT §9: 요청 기록이 있어야 place가 결과를 고른다."""
        self.request_file(req_id, mode=mode)
        if mode == "direct":
            path = self.folder / ".gg-work" / "figure-results" / (req_id + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            res = self.home / "res"
            res.mkdir(parents=True, exist_ok=True)
            path = res / (req_id + ".res.json")
        path.write_text(json.dumps(payload), encoding="utf-8")

    def app_result(self, images, slot="s1"):
        return {"v": 1, "id": "req-0001", "status": "done", "slot": slot,
                "page": {"url": "https://" + HOST + DETAIL_PATH,
                         "org": "농촌진흥청"},
                "images": images}

    def approved(self, sha, label=None, org="농촌진흥청"):
        return {"url": IMAGE02, "finalUrl": IMAGE02, "verdict": "approved",
                "reason": "ok", "sha256": sha, "token": "app-figure:" + sha,
                "kogl": 2, "org": org, "label": label}

    def test_zero_approved_photo_placeholder(self):
        self.result_file(self.app_result([
            {"url": IMAGE02, "finalUrl": IMAGE02, "verdict": "ambiguous",
             "reason": "kogl_3", "sha256": None, "token": None,
             "kogl": 3, "org": "농촌진흥청", "label": None}]))
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 home=self.home, environ={})
        self.assertEqual(result["status"], "needs_photo")
        text = self.manuscript.read_text(encoding="utf-8")
        self.assertIn("※ 이 자리에 품종 선택 사진 추가 필요", text)
        self.assertIsNotNone(gg_document.photo_guide(result["line"]))
        # 절 경계를 넘지 않는다
        section = text.split("## 나. 재배")[0]
        self.assertIn(result["line"], section)

    def test_placeholder_when_no_app_and_no_result(self):
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택", environ={})
        self.assertEqual(result["status"], "needs_photo")

    def test_one_approved_inserts_token_and_caption(self):
        sha = "b" * 64
        self.result_file(self.app_result([self.approved(sha)]))
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 home=self.home, environ={})
        self.assertEqual(result["status"], "placed")
        self.assertEqual(result["number"], 1)
        self.assertEqual(result["line"], "![](app-figure:%s)" % sha)
        self.assertEqual(result["caption"],
                         "그림 1. 품종 선택 (출처 : 농촌진흥청)")
        text = self.manuscript.read_text(encoding="utf-8")
        self.assertIn(result["line"], text)
        self.assertIn(result["caption"], text)
        self.assertLess(text.index(result["caption"]),
                        text.index("## 나. 재배"))

    def test_second_call_is_already_placed(self):
        sha = "b" * 64
        self.result_file(self.app_result([self.approved(sha)]))
        gg_figure.place(self.folder, "s1", "가. 품종 선택", home=self.home, environ={})
        again = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                home=self.home, environ={})
        self.assertEqual(again["status"], "already_placed")

    def test_caption_number_follows_existing(self):
        self.manuscript.write_text(MANUSCRIPT + "\n그림 3. 기존 그림 (출처 : 농촌진흥청)\n",
                                   encoding="utf-8")
        sha = "c" * 64
        self.result_file(self.app_result([self.approved(sha)]))
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 home=self.home, environ={})
        self.assertEqual(result["number"], 4)

    def test_two_approved_needs_choice_without_touching_manuscript(self):
        before = self.manuscript.read_bytes()
        self.result_file(self.app_result([
            self.approved("1" * 64, "A"), self.approved("2" * 64, "B")]))
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 home=self.home, environ={})
        self.assertEqual(result["status"], "needs_choice")
        self.assertIn("A·B·C 중 하나만 적어 주세요", result["question"])
        self.assertEqual([item["label"] for item in result["candidates"]], ["A", "B"])
        self.assertEqual(self.manuscript.read_bytes(), before)

    def test_two_approved_labels_assigned_when_app_omits_them(self):
        """앱 res에는 후보 이름이 없을 수 있다 — 순서대로 A·B를 붙여 묻는다."""
        before = self.manuscript.read_bytes()
        self.result_file(self.app_result([
            self.approved("1" * 64), self.approved("2" * 64)]))
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 home=self.home, environ={})
        self.assertEqual(result["status"], "needs_choice")
        self.assertEqual([item["label"] for item in result["candidates"]],
                         ["A", "B"])
        self.assertEqual(self.manuscript.read_bytes(), before)

    def write_choice(self, text, candidates=None):
        path = gg_figure._choice_path(self.home, self.folder, "s1")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"v": 1, "reqId": "req-0001", "slot": "s1",
                   "question": "A·B·C 중 하나만 적어 주세요",
                   "candidates": candidates or [
                       {"label": "A", "sha256": "1" * 64, "desc": "후보 A"},
                       {"label": "B", "sha256": "2" * 64, "desc": "후보 B"}],
                   "studentMessage": None if text is None else {"text": text, "at": 0}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_choose_matches_recorded_message_after_strip(self):
        self.result_file(self.app_result([
            self.approved("1" * 64, "A"), self.approved("2" * 64, "B")]))
        self.write_choice(" A ")
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 choose="A", home=self.home, environ=APP_ENV)
        self.assertEqual(result["status"], "placed")
        self.assertEqual(result["line"], "![](app-figure:%s)" % ("1" * 64))

    def test_choose_rejected_when_message_is_a_sentence(self):
        self.result_file(self.app_result([
            self.approved("1" * 64, "A"), self.approved("2" * 64, "B")]))
        before = self.manuscript.read_bytes()
        self.write_choice("B로 할게요")
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 choose="B", home=self.home, environ=APP_ENV)
        self.assertEqual(result["status"], "reask")
        self.assertEqual(result["reason"], "student_message_mismatch")
        self.assertEqual(self.manuscript.read_bytes(), before)

    def test_choose_blocked_without_student_message(self):
        self.result_file(self.app_result([
            self.approved("1" * 64, "A"), self.approved("2" * 64, "B")]))
        self.write_choice(None)
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 choose="A", home=self.home, environ=APP_ENV)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "no_student_message")

    def test_choose_not_a_candidate(self):
        self.result_file(self.app_result([
            self.approved("1" * 64, "A"), self.approved("2" * 64, "B")]))
        self.write_choice("D")
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 choose="D", home=self.home, environ={})
        self.assertEqual(result["status"], "reask")
        self.assertEqual(result["reason"], "not_a_candidate")

    def test_choose_candidate_hash_mismatch_blocks(self):
        self.result_file(self.app_result([
            self.approved("1" * 64, "A"), self.approved("2" * 64, "B")]))
        self.write_choice("A", candidates=[
            {"label": "A", "sha256": "9" * 64, "desc": "다른 그림"},
            {"label": "B", "sha256": "2" * 64, "desc": "후보 B"}])
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 choose="A", home=self.home, environ=APP_ENV)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "candidate_mismatch")

    def test_manual_image_inserts_local_markdown(self):
        image = self.folder / "figures" / "student" / "leaf.jpg"
        image.parent.mkdir(parents=True)
        image.write_bytes(fixture("nongsaro-image02.jpg"))
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 images=["figures/student/leaf.jpg"],
                                 org="농촌진흥청", environ={})
        self.assertEqual(result["status"], "placed")
        self.assertEqual(result["line"],
                         "![그림 1. 품종 선택 (출처 : 농촌진흥청)]"
                         "(figures/student/leaf.jpg)")
        self.assertIn(result["line"], self.manuscript.read_text(encoding="utf-8"))

    def test_manual_image_block_carries_a_caption_paragraph(self):
        """R-4: 직접 그림도 그림 줄 + 빈 줄 + 캡션 문단이어야 검사를 통과한다."""
        image = self.folder / "figures" / "student" / "leaf.jpg"
        image.parent.mkdir(parents=True)
        image.write_bytes(fixture("nongsaro-image02.jpg"))
        self.manuscript.write_text(
            MANUSCRIPT.replace("잎들깨 품종은 남천·동글2호·새봄이 있다.",
                               "그림 1에서 보듯 잎들깨 품종은 남천·동글2호·새봄이 있다."),
            encoding="utf-8")
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 images=["figures/student/leaf.jpg"],
                                 org="농촌진흥청", environ={})
        self.assertEqual(result["status"], "placed")
        text = self.manuscript.read_text(encoding="utf-8")
        caption = "그림 1. 품종 선택 (출처 : 농촌진흥청)"
        self.assertIn(result["line"] + "\n\n" + caption + "\n", text)
        kinds = [kind for kind, _ in gg_document.check(text, self.folder)]
        self.assertNotIn("missing_caption", kinds)
        self.assertNotIn("object_reference", kinds)
        one_line = text.replace("\n\n" + caption, "")
        self.assertIn(("missing_caption", "그림 객체의 캡션 없음"),
                      gg_document.check(one_line, self.folder))

    def test_manual_image_missing(self):
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 images=["figures/none.jpg"], org="농촌진흥청",
                                 environ={})
        self.assertEqual(result["reason"], "image_missing")

    def test_manual_image_needs_org(self):
        image = self.folder / "figures" / "student" / "leaf.jpg"
        image.parent.mkdir(parents=True)
        image.write_bytes(fixture("nongsaro-image02.jpg"))
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 images=["figures/student/leaf.jpg"], environ={})
        self.assertEqual(result["reason"], "no_org")

    def test_direct_result_file_is_used(self):
        sha = "d" * 64
        payload = self.app_result([self.approved(sha)])
        payload["mode"] = "direct"
        payload["images"][0]["path"] = "figures/public/%s.jpg" % sha
        self.result_file(payload, mode="direct")
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택", environ={})
        self.assertEqual(result["status"], "placed")
        self.assertTrue(result["line"].startswith(
            "![그림 1. 품종 선택 (출처 : 농촌진흥청)](figures/public/"))

    def test_heading_missing(self):
        result = gg_figure.place(self.folder, "s1", "다. 없는 절", environ={})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "heading_not_found")

    def test_manuscript_outside_folder(self):
        outside = Path(tempfile.mkdtemp(prefix="gg-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "x.md").write_text(MANUSCRIPT, encoding="utf-8")
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 manuscript="../" + outside.name + "/x.md", environ={})
        self.assertEqual(result["status"], "blocked")

    def test_invalid_slot(self):
        result = gg_figure.place(self.folder, "../s1", "가. 품종 선택", environ={})
        self.assertEqual(result["reason"], "invalid_slot")

    def test_manifest_missing(self):
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 manuscript="build/none.md", environ={})
        self.assertEqual(result["reason"], "manuscript_missing")


class TestReviewFixes(_ServerCase):
    """REVIEW-DOC-1 B-1·B-2·I-1·I-2·I-3·I-4·R-1·R-2 잠금 시험."""

    def setUp(self):
        self.server.routes.clear()
        self.folder = Path(tempfile.mkdtemp(prefix="gg-folder-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        (self.folder / "build").mkdir()
        (self.folder / "build" / "검토전_본문.md").write_text(MANUSCRIPT, encoding="utf-8")

    def fetch(self, page, images, slot="s1"):
        return gg_figure.fetch_direct(
            self.folder, page, images, slot, rules=[rule()],
            resolver=self.resolver, allow_loopback=True, tls_context=self.tls)

    def page(self, body):
        self.route(DETAIL_PATH, body.encode("utf-8"))

    # B-1 ---------------------------------------------------------------
    def test_redirect_target_must_be_the_page_reference(self):
        """B-1: 요청 URL이 아니라 리다이렉트를 따라간 주소로 실재를 본다."""
        start = "https://%s/ps/img/curation/a.jpg" % HOST
        final = "https://%s/ps/img/curation/third.jpg" % HOST
        self.page('<p>open_mark2.png</p><img src="%s">' % start)
        self.route("/ps/img/curation/a.jpg", status=302,
                   headers={"location": "/ps/img/curation/third.jpg"})
        self.route("/ps/img/curation/third.jpg", fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [start])
        self.assertEqual(result["images"][0]["verdict"], "ambiguous")
        self.assertEqual(result["images"][0]["reason"], "not_on_page")
        self.assertEqual(result["images"][0]["finalUrl"], final)

    def test_redirect_target_with_other_scheme_port_is_not_on_page(self):
        """B-1: 리다이렉트가 따라간 주소를 페이지가 http:443으로만 적으면 불일치다."""
        start = "https://%s/ps/img/curation/a.jpg" % HOST
        self.page('<p>open_mark2.png</p>'
                  '<img src="http://%s:443/ps/img/curation/final.jpg">' % HOST)
        self.route("/ps/img/curation/a.jpg", status=302,
                   headers={"location": "/ps/img/curation/final.jpg"})
        self.route("/ps/img/curation/final.jpg", fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [start])
        self.assertEqual(result["images"][0]["verdict"], "ambiguous")
        self.assertEqual(result["images"][0]["reason"], "not_on_page")

    # B-2 ---------------------------------------------------------------
    def test_anchor_with_long_attributes_sees_the_mark_after_the_tag(self):
        """B-2: 링크 태그에 긴 속성이 붙어도 태그 **뒤**의 '사진 제공'을 본다."""
        start = "https://%s/ps/img/curation/a.jpg" % HOST
        pad = ' data-note="' + "x" * 400 + '"'
        self.page('<p>open_mark2.png</p>'
                  '<a href="%s"%s>원본</a><span>사진 제공: 홍길동</span>'
                  % (start, pad))
        self.route("/ps/img/curation/a.jpg", fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [start])
        self.assertEqual(result["images"][0]["verdict"], "ambiguous")
        self.assertEqual(result["images"][0]["reason"],
                         "third_party_mark:사진 제공")

    # I-4 ---------------------------------------------------------------
    def test_direct_two_approved_asks_then_choose_places_without_app(self):
        """I-4: 앱 밖 직접 받기 결과도 2장이면 묻고, --choose로 넣는다."""
        self.page('<p>open_mark2.png</p>'
                  '<img src="%s"><img src="%s">' % (IMAGE02, IMAGE12))
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        self.route("/ps/img/curation/20240209/images/image12.jpg",
                   fixture("nongsaro-image12.jpg"))
        fetched = self.fetch(PAGE_URL, [IMAGE02, IMAGE12])
        self.assertTrue(fetched["needsChoice"])
        self.assertEqual([item["label"] for item in fetched["images"]], ["A", "B"])
        path = self.folder / ".gg-work" / "figure-requests" / "s1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": 1, "slot": "s1",
                                    "reqId": fetched["id"], "mode": "direct",
                                    "at": 0}), encoding="utf-8")
        (self.folder / ".gg-work" / "figure-results").mkdir(exist_ok=True)
        (self.folder / ".gg-work" / "figure-results"
         / (fetched["id"] + ".json")).write_text(
             json.dumps(fetched), encoding="utf-8")

        asked = gg_figure.place(self.folder, "s1", "가. 품종 선택", environ={})
        self.assertEqual(asked["status"], "needs_choice")
        placed = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 choose="A", environ={})
        self.assertEqual(placed["status"], "placed")
        self.assertTrue(placed["line"].startswith(
            "![그림 1. 품종 선택 (출처 : 농촌진흥청)](figures/public/"))

    # I-3 ---------------------------------------------------------------
    def test_result_from_other_request_is_not_picked(self):
        """I-3: 슬롯·mtime이 아니라 요청 id로 고른다."""
        home = Path(tempfile.mkdtemp(prefix="gg-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        (self.folder / ".gg-work" / "figure-requests").mkdir(parents=True)
        (self.folder / ".gg-work" / "figure-requests" / "s1.json").write_text(
            json.dumps({"v": 1, "slot": "s1", "reqId": "req-0001",
                        "mode": "app", "at": 0}), encoding="utf-8")
        res = home / "res"
        res.mkdir()
        (res / "req-0002.res.json").write_text(json.dumps({
            "v": 1, "id": "req-0002", "status": "done", "slot": "s2",
            "images": [{"url": IMAGE02, "verdict": "approved",
                        "sha256": "b" * 64}]}), encoding="utf-8")
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 home=home, environ={})
        self.assertEqual(result["status"], "needs_photo")

    def test_choice_from_other_request_is_ignored(self):
        """I-3: 후보표 reqId가 결과 id와 다르면 쓰지 않는다."""
        home = Path(tempfile.mkdtemp(prefix="gg-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        (self.folder / ".gg-work" / "figure-requests").mkdir(parents=True)
        (self.folder / ".gg-work" / "figure-requests" / "s1.json").write_text(
            json.dumps({"v": 1, "slot": "s1", "reqId": "req-0001",
                        "mode": "app", "at": 0}), encoding="utf-8")
        res = home / "res"
        res.mkdir()
        images = [{"url": IMAGE02, "finalUrl": IMAGE02, "verdict": "approved",
                   "reason": "ok", "sha256": s, "kogl": 2, "org": "농촌진흥청",
                   "label": None} for s in ("1" * 64, "2" * 64)]
        (res / "req-0001.res.json").write_text(json.dumps({
            "v": 1, "id": "req-0001", "status": "done", "slot": "s1",
            "images": images}), encoding="utf-8")
        path = gg_figure._choice_path(home, self.folder, "s1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "v": 1, "reqId": "req-9999", "slot": "s1", "question": "?",
            "candidates": [{"label": "A", "sha256": "1" * 64, "desc": "낡은 표"},
                           {"label": "B", "sha256": "2" * 64, "desc": "낡은 표"}],
            "studentMessage": None}), encoding="utf-8")
        result = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 home=home, environ={})
        self.assertEqual(result["status"], "needs_choice")
        self.assertNotIn("낡은 표", [item["desc"] for item in result["candidates"]])

    def test_rejected_new_request_replaces_the_old_slot_record(self):
        """I-04 r5: 거절돼도 슬롯은 새 reqId를 유지해 옛 그림을 쓰지 않는다."""
        home = Path(tempfile.mkdtemp(prefix="gg-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        old_path = self.folder / ".gg-work" / "figure-requests" / "s1.json"
        old_path.parent.mkdir(parents=True)
        old_path.write_text(json.dumps({
            "v": 1, "slot": "s1", "reqId": "req-0001", "mode": "app", "at": 0}),
            encoding="utf-8")
        res = home / "res"
        res.mkdir()
        (res / "req-0001.res.json").write_text(json.dumps({
            "v": 1, "id": "req-0001", "status": "done", "slot": "s1",
            "images": [{"url": IMAGE02, "finalUrl": IMAGE02,
                        "verdict": "approved", "reason": "ok",
                        "sha256": "a" * 64, "kogl": 2, "org": "농촌진흥청",
                        "label": None}]}), encoding="utf-8")

        # 새 앱 요청은 rejected: cancelled로 끝난다.
        (res / "bbbbbbbbbbbbbbbb.res.json").write_text(json.dumps({
            "v": 1, "id": "bbbbbbbbbbbbbbbb", "status": "rejected", "slot": "s1",
            "reason": "cancelled", "images": []}), encoding="utf-8")
        now_ms = int(time.time() * 1000)
        (home / "figure-channel.json").write_text(json.dumps(
            {"v": 1, "updatedAt": now_ms}), encoding="utf-8")
        (home / "turn.json").write_text(json.dumps(
            {"v": 1, "turnId": "t1", "folder": str(self.folder.resolve()),
             "startedAt": now_ms - 1000}), encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(gg_figure.secrets, "token_hex",
                               return_value="bbbbbbbbbbbbbbbb"), \
                mock.patch("sys.stdout", out), \
                mock.patch.dict(os.environ, {"KNUAF_GUI": "1",
                                             "KNUAF_FIGURE_HOME": str(home)}):
            code = gg_figure.main(["fetch", str(self.folder), "--slot", "s1",
                                   "--page", PAGE_URL, "--image", IMAGE02])
        payload = json.loads(out.getvalue().strip())
        self.assertEqual(code, 2)
        self.assertEqual(payload["id"], "bbbbbbbbbbbbbbbb")
        self.assertEqual(gg_figure._read_request(self.folder, "s1")["reqId"],
                         "bbbbbbbbbbbbbbbb")

        placed = gg_figure.place(self.folder, "s1", "가. 품종 선택",
                                 home=home, environ={})
        self.assertEqual(placed["status"], "needs_photo")
        after = (self.folder / "build" / "검토전_본문.md").read_text(encoding="utf-8")
        # 옛 승인 그림 토큰이 들어가지 않고, 사진 자리 한 줄만 남는다.
        self.assertNotIn("app-figure:", after)
        self.assertIn(placed["line"], after)
        self.assertIn("# Ⅰ. 서론", after)

    # I-2 ---------------------------------------------------------------
    def test_non_ascii_image_path_is_fetched(self):
        """I-2: 한글 경로도 퍼센트 인코딩해 받는다(예전엔 UnicodeEncodeError)."""
        target = "/ps/img/curation/%ED%95%9C%EA%B8%80.jpg"
        raw = "https://%s/ps/img/curation/한글.jpg" % HOST
        self.page('<p>open_mark2.png</p><img src="%s">' % target)
        self.route(target, fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [raw])
        self.assertEqual(result["images"][0]["verdict"], "approved")

    def test_bad_image_body_is_a_rejected_record(self):
        """I-2: 그림 본문이 깨져도 그 장만 rejected가 되고 다음 장으로 간다."""
        bad = "https://%s/ps/img/curation/bad.jpg" % HOST
        good = "https://%s/ps/img/curation/20240209/images/image02.jpg" % HOST
        self.page('<p>open_mark2.png</p><img src="%s"><img src="%s">'
                  % (bad, good))
        self.route("/ps/img/curation/bad.jpg", b"not an image",
                   headers={"content-type": "image/jpeg"})
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [bad, good])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["images"][0]["verdict"], "rejected")
        self.assertEqual(result["images"][0]["reason"], "bad_image")
        self.assertEqual(result["images"][1]["verdict"], "approved")

    def test_unexpected_error_still_prints_json(self):
        """I-2: 남은 예외도 traceback 대신 JSON 한 줄 + 종료코드 2."""
        out = io.StringIO()
        with mock.patch.object(gg_figure, "fetch_direct",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(gg_figure, "_is_app_mode", return_value=False):
            with mock.patch("sys.stdout", out):
                code = gg_figure.main(["fetch", str(self.folder), "--slot", "s1",
                                       "--page", PAGE_URL, "--image", IMAGE02])
        payload = json.loads(out.getvalue().strip())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reason"], "internal")
        self.assertIn("RuntimeError: boom", payload["detail"])

    def test_truncated_gif_header_does_not_crash(self):
        self.assertEqual(gg_figure._image_size(b"GIF89a" + bytes(7))[3], 1)
        self.assertEqual(gg_figure._image_size(b"GIF89a" + bytes(9) + b"\x2c")[3], 1)

    # R-1 ---------------------------------------------------------------
    def test_slow_drip_is_cut_by_the_request_deadline(self):
        """R-1: 한 바이트씩 흘려도 요청 전체 상한에서 끊긴다."""

        def drip(handler):
            handler.send_response(200)
            handler.send_header("content-type", "image/jpeg")
            handler.send_header("content-length", "100")
            handler.end_headers()
            try:
                for _ in range(16):
                    handler.wfile.write(b"\xff")
                    handler.wfile.flush()
                    time.sleep(0.5)
            except OSError:
                pass

        self.route("/ps/img/curation/slow.jpg", drip)
        limiter = {"bytes": 0, "deadline": time.monotonic() + 2.0}
        started = time.monotonic()
        with self.assertRaises(gg_figure.FigureError) as caught:
            gg_figure.http_get("https://%s/ps/img/curation/slow.jpg" % HOST,
                               rule=rule(), kind="image", resolver=self.resolver,
                               allow_loopback=True, tls_context=self.tls,
                               limiter=limiter)
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.reason, "timeout")
        self.assertLess(elapsed, 5.0)

    def test_header_drip_is_cut_by_the_request_deadline(self):
        """R-1: 헤더를 조금씩 흘리면 getresponse()도 요청 상한(0.20 s)에 끊긴다."""
        header = b"HTTP/1.0 200 OK\r\ncontent-type: image/jpeg\r\n"
        header += b"x-pad: " + b"a" * 60 + b"\r\n\r\n"

        def drip(handler):
            try:
                for index in range(len(header)):
                    handler.wfile.write(header[index:index + 1])
                    handler.wfile.flush()
                    time.sleep(0.02)
            except OSError:
                pass

        self.route("/ps/img/curation/headerdrip.jpg", drip)
        limiter = {"bytes": 0, "deadline": time.monotonic() + 0.20}
        started = time.monotonic()
        with self.assertRaises(gg_figure.FigureError) as caught:
            gg_figure.http_get(
                "https://%s/ps/img/curation/headerdrip.jpg" % HOST,
                rule=rule(), kind="image", resolver=self.resolver,
                allow_loopback=True, tls_context=self.tls, limiter=limiter)
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.reason, "timeout")
        # 예전: 요청 상한을 넘겨 0.615 s에야 끊겼다.
        self.assertLess(elapsed, 0.5, "header drip elapsed %.3fs" % elapsed)

    def test_body_drip_is_cut_by_the_file_deadline(self):
        """R-1: 본문 흘리기는 요청 상한(0.60 s)이 아니라 파일 상한(0.15 s)에 끊긴다."""
        def drip(handler):
            handler.send_response(200)
            handler.send_header("content-type", "image/jpeg")
            handler.send_header("content-length", "100")
            handler.end_headers()
            try:
                for _ in range(16):
                    handler.wfile.write(b"\xff")
                    handler.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        self.route("/ps/img/curation/bodydrip.jpg", drip)
        limiter = {"bytes": 0, "deadline": time.monotonic() + 0.60}
        started = time.monotonic()
        with mock.patch.object(gg_figure, "TIMEOUT_FILE_SECONDS", 0.15):
            with self.assertRaises(gg_figure.FigureError) as caught:
                gg_figure.http_get(
                    "https://%s/ps/img/curation/bodydrip.jpg" % HOST,
                    rule=rule(), kind="image", resolver=self.resolver,
                    allow_loopback=True, tls_context=self.tls, limiter=limiter)
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.reason, "timeout")
        # 예전: 파일 상한을 무시하고 요청 상한에서 0.610 s에 끊겼다.
        self.assertLess(elapsed, 0.5, "body drip elapsed %.3fs" % elapsed)

    # R-2 ---------------------------------------------------------------
    def test_symlinked_figures_dir_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="gg-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (self.folder / "figures").symlink_to(outside, target_is_directory=True)
        self.page('<p>open_mark2.png</p><img src="%s">' % IMAGE02)
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [IMAGE02])
        self.assertEqual(result["images"][0]["verdict"], "rejected")
        self.assertEqual(result["images"][0]["reason"], "store_symlink")
        self.assertEqual(list(outside.iterdir()), [])

    def _counters(self, **values):
        path = self.folder / ".gg-work" / "figure-counters.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values), encoding="utf-8")

    def test_symlinked_receipts_file_is_refused(self):
        """R-2: receipt 파일이 폴더 밖 링크면 받지 않고 바깥 파일도 건드리지 않는다."""
        outside = Path(tempfile.mkdtemp(prefix="gg-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        sentinel = outside / "sentinel.jsonl"
        sentinel.write_text("", encoding="utf-8")
        work = self.folder / ".gg-work"
        work.mkdir()
        (work / "figure-receipts.jsonl").symlink_to(sentinel)

        self.page('<p>open_mark2.png</p><img src="%s">' % IMAGE02)
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [IMAGE02])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "store_symlink")
        self.assertEqual(result["detail"], "figure-receipts.jsonl")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "")

    def test_symlinked_work_dir_is_refused(self):
        """R-2: `.gg-work`가 폴더 밖 링크면 바깥에 계수·receipt를 만들지 않는다."""
        outside = Path(tempfile.mkdtemp(prefix="gg-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (self.folder / ".gg-work").symlink_to(outside, target_is_directory=True)

        self.page('<p>open_mark2.png</p><img src="%s">' % IMAGE02)
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [IMAGE02])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "slot_record_failed")
        self.assertIn(".gg-work", result["detail"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_daily_file_quota(self):
        self._counters(v=1, day=time.strftime("%Y-%m-%d", time.gmtime()),
                       files=gg_figure.MAX_STORE_FILES_PER_DAY, bytes=0)
        self.page('<p>open_mark2.png</p><img src="%s">' % IMAGE02)
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [IMAGE02])
        self.assertEqual(result["images"][0]["reason"], "store_quota_files")

    def test_total_byte_quota(self):
        self._counters(v=1, day=time.strftime("%Y-%m-%d", time.gmtime()),
                       files=0, bytes=gg_figure.MAX_STORE_BYTES)
        self.page('<p>open_mark2.png</p><img src="%s">' % IMAGE02)
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [IMAGE02])
        self.assertEqual(result["images"][0]["reason"], "store_quota_bytes")

    def test_receipt_line_shape(self):
        self.page('<p>open_mark2.png</p><img src="%s">' % IMAGE02)
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [IMAGE02])
        lines = (self.folder / ".gg-work" / "figure-receipts.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        line = json.loads(lines[0])
        self.assertEqual(set(line), {"v", "at", "reqId", "folderHash", "slot",
                                     "page", "image"})
        self.assertEqual(line["reqId"], result["id"])
        self.assertEqual(line["slot"], "s1")
        self.assertEqual(line["image"]["verdict"], "approved")
        self.assertEqual(line["image"]["sha256"],
                         result["images"][0]["sha256"])
        self.assertEqual(set(line["page"]), {"url", "sha256", "marks"})

    def test_type2_notice_is_reported(self):
        self.page('<p>open_mark2.png</p><img src="%s">' % IMAGE02)
        self.route("/ps/img/curation/20240209/images/image02.jpg",
                   fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL, [IMAGE02])
        self.assertEqual(result["notices"], [gg_figure.TYPE2_NOTICE])

    # R-3 ---------------------------------------------------------------
    def test_path_rule_must_match_the_whole_path(self):
        """R-3: 규칙 정규식은 경로 전체 일치(fullmatch)다."""
        # 앞부분만 맞는 규칙: search면 통과, fullmatch면 거절이어야 한다.
        pattern = re.compile("^/ps/img/curation/[^/]+")
        with self.assertRaises(gg_figure.FigureError) as caught:
            gg_figure.normalize_url(IMAGE02, host=HOST, path_pattern=pattern)
        self.assertEqual(caught.exception.reason, "path_not_allowed")
        whole = re.compile("^/ps/img/curation/.+")
        self.assertEqual(
            gg_figure.normalize_url(IMAGE02, host=HOST, path_pattern=whole),
            IMAGE02)

    def test_page_charset_header_is_honoured(self):
        """R-3: 페이지 Content-Type의 charset으로 푼다(고정 utf-8 아님)."""
        page = ('<p>open_mark2.png</p>'
                '<img src="/ps/img/curation/\ud55c\uae00.jpg">')
        self.route(DETAIL_PATH, page.encode("cp949"),
                   headers={"content-type": "text/html; charset=cp949"})
        target = "/ps/img/curation/%ED%95%9C%EA%B8%80.jpg"
        self.route(target, fixture("nongsaro-image02.jpg"))
        result = self.fetch(PAGE_URL,
                            ["https://%s/ps/img/curation/\ud55c\uae00.jpg" % HOST])
        self.assertEqual(result["images"][0]["verdict"], "approved")


# --------------------------------------------------------------------------
# pageQuery (DESIGN-PQ r1) — 앱과 글자 그대로 같은 공용 벡터
# --------------------------------------------------------------------------
VECTORS_FILE = FIXTURES / "pagequery-vectors.json"
VECTOR_TOKEN = re.compile(r"\{\{([^{}]*?)\*(\d+)\}\}")


def _vector_expand(value):
    """벡터의 `{{글자*수}}`를 실제 글자로 펼친다(길이 경계를 정확히 맞추려고)."""
    if isinstance(value, str):
        return VECTOR_TOKEN.sub(lambda m: m.group(1) * int(m.group(2)), value)
    if isinstance(value, list):
        return [_vector_expand(item) for item in value]
    if isinstance(value, dict):
        return {_vector_expand(key): _vector_expand(item)
                for key, item in value.items()}
    return value


def _vectors():
    return _vector_expand(json.loads(VECTORS_FILE.read_text(encoding="utf-8")))


def _vector_rules(data):
    """벡터의 `rules`(규칙 파일 내용)를 임시 파일에 써서 진짜 로더로 읽는다."""
    folder = Path(tempfile.mkdtemp(prefix="gg-vectors-"))
    path = folder / "figure-sources.json"
    path.write_text(json.dumps(data["rules"], ensure_ascii=False),
                    encoding="utf-8")
    return gg_figure.load_rules(path), folder


class TestPageQueryLoad(unittest.TestCase):
    """벡터 `load`·`loadFile`·`loadText`(로더 G1~G5·I1~I7)."""

    @classmethod
    def setUpClass(cls):
        cls.data = _vectors()

    def merged(self, override):
        """`loadBase`에 얕게 덮어쓴다 — 값이 null이면 그 칸을 지운다."""
        item = dict(self.data["loadBase"])
        for key, value in override.items():
            if value is None:
                item.pop(key, None)
            else:
                item[key] = value
        return item

    def test_load_vectors(self):
        """설계 §6: 항목 하나만 든 파일을 진짜 로더로 읽어 규칙 수가 1인지 0인지."""
        for override, expected, note in self.data["load"]:
            with self.subTest(note=note):
                folder = Path(tempfile.mkdtemp(prefix="gg-vectors-"))
                self.addCleanup(shutil.rmtree, folder, True)
                path = folder / "figure-sources.json"
                path.write_text(
                    json.dumps({"v": 1, "rules": [self.merged(override)]},
                               ensure_ascii=False),
                    encoding="utf-8")
                count = len(gg_figure.load_rules(path))
                self.assertEqual(count, 1 if expected else 0, note)

    def test_load_file_vectors(self):
        for document, expected, note in self.data["loadFile"]:
            with self.subTest(note=note):
                folder = Path(tempfile.mkdtemp(prefix="gg-vectors-"))
                self.addCleanup(shutil.rmtree, folder, True)
                path = folder / "figure-sources.json"
                path.write_text(json.dumps(document, ensure_ascii=False),
                                encoding="utf-8")
                ids = [rule["id"] for rule in gg_figure.load_rules(path)]
                self.assertEqual(ids, expected, note)

    def test_load_text_vectors(self):
        for text, expected, probes, note in self.data["loadText"]:
            with self.subTest(note=note):
                folder = Path(tempfile.mkdtemp(prefix="gg-vectors-"))
                self.addCleanup(shutil.rmtree, folder, True)
                path = folder / "figure-sources.json"
                path.write_text(text, encoding="utf-8")
                rules = gg_figure.load_rules(path)
                self.assertEqual([rule["id"] for rule in rules], expected, note)
                for url, want in probes:
                    chosen = gg_figure.select_page_rule(url, rules)
                    got = None if chosen is None else chosen[0]["id"]
                    self.assertEqual(got, want, note + " <- " + url)

    def test_loader_rejects_non_standard_json_constants(self):
        # G1: 표준 JSON이 아니면 파일째 거절 — 앱 JSON.parse와 같은 답(PQI-01).
        base = json.loads(json.dumps(self.data["loadBase"]))
        good = json.dumps({"v": 1, "rules": [base]})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "figure-sources.json"
            path.write_text(good, encoding="utf-8")
            self.assertEqual(len(gg_figure.load_rules(path)), 1)
            path.write_text(good.replace('"v": 1', '"v": 1.0'), encoding="utf-8")
            self.assertEqual(len(gg_figure.load_rules(path)), 1)
            for const in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(const=const):
                    path.write_text(good[:-1] + ', "extra": ' + const + "}", encoding="utf-8")
                    self.assertEqual(gg_figure.load_rules(path), [])

    def test_loader_rejects_line_breaks_in_host_names_and_values(self):
        # Python/JS의 `$`가 끝 줄바꿈 앞을 허용해 승인 범위를 넓히지 않게 한다.
        overrides = [
            {"host": "h.example.kr\n"},
            {"host": "h\n.example.kr"},
            {"pageQuery": {"k\n": {"literal": "v"}}},
            {"pageQuery": {"k": {"literal": "v\n"}}},
            {"pageQuery": {"k": {"enum": ["v\n"]}}},
            {"pageQuery": {"k": {"digits": 1}}},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "figure-sources.json"
            for override in overrides:
                with self.subTest(override=override):
                    document = {"v": 1, "rules": [self.merged(override)]}
                    path.write_text(json.dumps(document), encoding="utf-8")
                    self.assertEqual(gg_figure.load_rules(path), [])


class TestPageQuerySelect(unittest.TestCase):
    """벡터 `page`·`image`(주소 판정과 항목 고르기)."""

    @classmethod
    def setUpClass(cls):
        cls.data = _vectors()
        cls.rules, folder = _vector_rules(cls.data)
        cls.addClassCleanup(shutil.rmtree, folder, True)

    def test_page_vectors(self):
        for url, expected, note in self.data["page"]:
            with self.subTest(note=note):
                chosen = gg_figure.select_page_rule(url, self.rules)
                got = None if chosen is None else chosen[0]["id"]
                self.assertEqual(got, expected, note)
                if expected is not None and chosen[0]["rule"] in gg_figure.QUERY_KINDS:
                    # 새 항목은 적힌 주소를 그대로 보낸다(다듬지 않는다).
                    self.assertEqual(chosen[1], url, note)

    def test_image_vectors(self):
        for page, image, expected, note in self.data["image"]:
            with self.subTest(note=note):
                chosen = gg_figure.select_page_rule(page, self.rules)
                self.assertIsNotNone(chosen, note)
                rule = chosen[0]
                try:
                    gg_figure.normalize_url(image, host=rule["host"],
                                            path_pattern=rule["imagePath"])
                    ok = True
                except gg_figure.FigureError:
                    ok = False
                self.assertEqual(ok, expected, note)

    def test_real_rule_file_loads(self):
        path = SCRIPTS.parent / "references" / "figure-sources.json"
        ids = [rule["id"] for rule in gg_figure.load_rules(path)]
        self.assertEqual(ids, [])


class TestPageQueryFlow(_ServerCase):
    """벡터 `flow`(리다이렉트 — 새 항목은 아예 따라가지 않고, 옛 항목은 한 항목 안에서만)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = _vectors()
        cls.rules, folder = _vector_rules(cls.data)
        cls.addClassCleanup(shutil.rmtree, folder, True)

    def setUp(self):
        self.server.routes.clear()
        self.server.seen.clear()
        self.server.hits.clear()

    def path_of(self, url):
        parts = urllib.parse.urlsplit(url)
        path = parts.path or "/"
        return path + ("" if not parts.query else "?" + parts.query)

    def run_flow(self, kind, page, image, locations):
        """벡터 하나 → (통과했나, 실제 전송한 (호스트, 요청 대상) 목록)."""
        self.server.routes.clear()
        self.server.hits.clear()
        chosen = gg_figure.select_page_rule(page, self.rules)
        if chosen is None:
            return False, []
        rule, page_target = chosen
        if kind == "page":
            url, follow = page_target, rule.get("rule") not in gg_figure.QUERY_KINDS
        else:
            url, follow = image, True
        # 상대 Location은 직전 주소에 이어 붙여 서버의 다음 응답을 놓는다.
        steps = [url]
        for location in locations:
            steps.append(urllib.parse.urljoin(steps[-1], location))
        self.route(self.path_of(steps[-1]), b"ok")
        for index, location in enumerate(locations):
            self.route(self.path_of(steps[index]), status=302,
                       headers={"location": location})
        try:
            gg_figure.http_get(url, rule=rule, kind=kind,
                               resolver=self.resolver, allow_loopback=True,
                               tls_context=self.tls, follow_redirects=follow)
            ok = True
        except gg_figure.FigureError:
            ok = False
        return ok, list(self.server.hits)

    def test_flow_vectors(self):
        # §6 flow 순서: 성공뿐 아니라 거절 지점 직전까지의 전송 수를 고정한다.
        # 4번째 옛 페이지 리다이렉트는 응답까지만 받고 5번째 주소는 안 보낸다.
        sent_counts = (1, 1, 1, 1, 1, 2, 4, 4, 1, 2, 1, 1, 1, 2, 1, 2, 2)
        for index, vector in enumerate(self.data["flow"]):
            kind, page, image, locations, expected, note = vector
            with self.subTest(note=note):
                ok, sent = self.run_flow(kind, page, image, locations)
                self.assertEqual(ok, expected, note)
                steps = [page if kind == "page" else image]
                for location in locations:
                    steps.append(urllib.parse.urljoin(steps[-1], location))
                expected_sent = [
                    (urllib.parse.urlsplit(url).netloc, self.path_of(url))
                    for url in steps[:sent_counts[index]]
                ]
                self.assertEqual(sent, expected_sent, note)

    def fetch_direct(self, folder, page, images):
        return gg_figure.fetch_direct(
            folder, page, images, "pq", rules=self.rules,
            resolver=self.resolver, allow_loopback=True, tls_context=self.tls)

    def test_second_query_rule_approves_and_records_its_id(self):
        """겹치는 imagePath여도 처음 항목이 아닌 페이지 항목의 권리·증명을 쓴다."""
        page, image, _, _ = self.data["image"][1]
        body = fixture("nongsaro-image02.jpg")
        for kogl in (1, 2):
            with self.subTest(kogl=kogl), tempfile.TemporaryDirectory() as folder:
                self.server.hits.clear()
                html = '<p>open_mark%d.png</p><img src="%s">' % (kogl, image)
                self.route(self.path_of(page), html.encode("utf-8"))
                self.route(self.path_of(image), body)
                result = self.fetch_direct(folder, page, [image])
                self.assertEqual(result["status"], "done")
                self.assertEqual(result["page"]["ruleId"], "t-card")
                record = result["images"][0]
                self.assertEqual(record["verdict"], "approved")
                self.assertEqual(record["kogl"], kogl)
                self.assertEqual(record["org"], "시험기관(카드뉴스)")
                self.assertEqual((Path(folder) / record["path"]).read_bytes(), body)
                receipt = json.loads((Path(folder) / ".gg-work" /
                                      "figure-receipts.jsonl").read_text("utf-8"))
                self.assertEqual(receipt["image"]["ruleId"], "t-card")
                self.assertEqual(self.server.hits, [
                    ("www.example.go.kr", self.path_of(page)),
                    ("www.example.go.kr", self.path_of(image)),
                ])

    def test_query_page_3xx_blocks_before_next_request(self):
        """http_get 선택 인자만이 아니라 fetch_direct의 새 페이지 배선도 잠근다."""
        page, image, _, _ = self.data["image"][1]
        location = page.replace("dataNo=1", "dataNo=2")
        for status in (300, 301, 302, 303, 304, 305, 306, 307, 308, 399):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                self.server.hits.clear()
                self.route(self.path_of(page), status=status,
                           headers={"location": location})
                self.route(self.path_of(location), b"ok")
                result = self.fetch_direct(folder, page, [image])
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], "redirect_not_allowed")
                self.assertEqual(self.server.hits, [
                    ("www.example.go.kr", self.path_of(page)),
                ])
                self.assertFalse((Path(folder) / "figures").exists())

    def test_invalid_query_is_blocked_without_request(self):
        page, image, _, _ = self.data["image"][1]
        with tempfile.TemporaryDirectory() as folder:
            result = self.fetch_direct(folder, page + "&pageNo=1", [image])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "no_rule")
            self.assertEqual(self.server.hits, [])


def candidate_vectors():
    return _vector_expand(json.loads(
        (FIXTURES / "candidates-vectors.json").read_text(encoding="utf-8")))


def candidate_rule():
    return gg_figure._load_rule(candidate_vectors()["rule"])


def list_result(req_id="abcdefgh", **changes):
    data = {"v": 1, "id": req_id, "status": "done", "slot": "s1",
            "needsChoice": False, "images": [], "kogl": 2, "org": "시험 기관",
            "candidates": [{"url": "https://example.gov/files/a.png", "credit": "none"}],
            "candidatesTruncated": False}
    data.update(changes)
    return data


class TestCandidateVectors(unittest.TestCase):
    def test_shared_vectors(self):
        data = candidate_vectors()
        selected = gg_figure._load_rule(data["rule"])
        for html, page, urls, truncated, note in data["vectors"]:
            with self.subTest(note=note):
                candidates, cut = gg_figure.extract_candidates(html, page, selected)
                self.assertEqual([item["url"] for item in candidates], urls)
                self.assertEqual(cut, truncated)
                credit = "credit" if note in ("credit in alt", "credit adjacent") else "none"
                self.assertEqual(candidates, [{"url": url, "credit": credit} for url in urls])
                dumped = json.dumps(candidates, ensure_ascii=False)
                for word in ("이전 지시", "사이트 지시", "\u202e", "😀"):
                    self.assertNotIn(word, dumped)

    def test_real_distribution_excludes_nongsaro(self):
        path = SCRIPTS.parent / "references" / "figure-sources.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in raw["rules"]:
            self.assertNotEqual(item["id"], "nongsaro-curation")
            self.assertNotEqual(item["host"], HOST)
            self.assertNotIn("fragment", item)
            self.assertNotIn("grade", item)
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "rules.json"
            without = dict(raw)
            without.pop("removed", None)
            p.write_text(json.dumps(without), encoding="utf-8")
            self.assertEqual(gg_figure.load_rules(path), gg_figure.load_rules(p))
            with mock.patch.object(gg_figure, "http_get") as get:
                result = gg_figure.fetch_direct(temp, PAGE_URL, [IMAGE02], "s1")
            self.assertIn(result["reason"], ("no_rules", "no_rule"))
            get.assert_not_called()


class TestListResultValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / "res").mkdir()

    def read(self, data, mode="list"):
        (self.home / "res" / "abcdefgh.res.json").write_text(
            json.dumps(data), encoding="utf-8")
        return gg_figure.read_result(self.home, "abcdefgh", mode, rule=candidate_rule())

    def test_valid_and_rejected_and_fetch_mode(self):
        self.assertEqual(self.read(list_result()), list_result())
        rejected = self.read(list_result(status="rejected", reason="rule_missing"))
        self.assertEqual(rejected, {"status": "blocked", "reason": "rule_missing"})
        with self.assertRaises(gg_figure.FigureError):
            self.read(list_result(), "fetch")

    def test_shared_expected_app_results_pass_real_reader_and_next_request(self):
        # 양쪽 추출 시험이 같은 벡터 기대값을 사용한다. 완성 결과 봉투째 읽는다.
        selected = candidate_rule()
        for _html, page, urls, truncated, note in candidate_vectors()["vectors"]:
            with self.subTest(note=note):
                credit = "credit" if note in ("credit in alt", "credit adjacent") else "none"
                result = list_result(candidates=[{"url": url, "credit": credit} for url in urls],
                                     candidatesTruncated=truncated)
                self.assertEqual(self.read(result), result)
                for url in urls:
                    request = gg_figure.request_payload("abcdefgh", "t", "s1", page, [url])
                    self.assertEqual(request["images"], [url])
                    self.assertEqual(gg_figure.normalize_url(url, host=selected["host"],
                        path_pattern=selected["imagePath"]), url)

    def test_ambiguous_app_candidate_spellings_are_rejected(self):
        for path in ("/files/a.png?", "/files/a.png?#", "/files/x/%2e%2e/a.png",
                     "/files/%2E/a.png", "/files/.%2e/a.png", "/files/x//a.png"):
            with self.subTest(path=path), self.assertRaises(gg_figure.FigureError) as caught:
                self.read(list_result(candidates=[{
                    "url": "https://example.gov" + path, "credit": "none"}]))
            self.assertEqual(caught.exception.reason, "invalid_result")

    def test_invalid_urls(self):
        for url in ("https://", "https://other.gov/files/a.png",
                    "https://example.gov/other/a.png", "https://example.gov:8443/files/a.png",
                    "https://example.gov:443/files/a.png", "https://127.0.0.1/files/a.png",
                    "https://example.gov/files/" + "a" * 2049,
                    "https://example.gov/files/a b.png", "https://example.gov/files/한글.png",
                    "https://example.gov/files/%zz", "http://example.gov/files/a.png",
                    "https://EXAMPLE.GOV/files/a.png", "https://example.gov/files/a.png#x"):
            with self.subTest(url=url), self.assertRaises(gg_figure.FigureError) as caught:
                self.read(list_result(candidates=[{"url": url, "credit": "none"}]))
            self.assertEqual(caught.exception.reason, "invalid_result")

    def test_invalid_schemas(self):
        good = list_result()
        bad = [{key: value for key, value in good.items() if key != missing}
               for missing in ("candidates", "org", "kogl")]
        bad += [list_result(**{key: value}) for key, value in (
            ("candidates", good["candidates"] * 31),
            ("candidates", good["candidates"] * 2),
            ("candidates", [dict(good["candidates"][0], alt="site prose")]),
            ("candidates", [dict(good["candidates"][0], credit="unknown")]),
            ("candidates", None), ("kogl", 0), ("kogl", 5), ("kogl", "2"),
            ("kogl", True), ("candidatesTruncated", "false"),
            ("images", [{"url": IMAGE02}]), ("needsChoice", True),
            ("status", "pending"), ("slot", None), ("id", "different"), ("v", 2))]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(gg_figure.FigureError) as caught:
                self.read(data)
            self.assertEqual(caught.exception.reason, "invalid_result")

    def test_empty_request_only_in_list_mode(self):
        self.assertEqual(gg_figure.request_payload(
            "abcdefgh", "t1", "s1", PAGE_URL, [], "list")["images"], [])
        for images in (None, [], [IMAGE02] * 4):
            with self.subTest(images=images), self.assertRaises(gg_figure.FigureError):
                gg_figure.request_payload("abcdefgh", "t1", "s1", PAGE_URL, images)
        with self.assertRaises(gg_figure.FigureError):
            gg_figure.request_payload("abcdefgh", "t1", "s1", PAGE_URL, [IMAGE02], "list")


class TestDirectLists(unittest.TestCase):
    def test_page_only_and_work_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root / ".gg-work"
            work.mkdir()
            (work / "keep").write_bytes(b"old work")
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            for mark, expected in (("open_mark4.png", 4), ("", None),
                                   ("open_mark1.png open_mark2.png", None)):
                html = (mark + "<img src='/files/포스터_vf.png'>").encode()
                with mock.patch.object(gg_figure, "http_get", return_value=(
                        "https://example.gov/files/article/page.html", 200, {}, html)) as get, \
                        mock.patch.object(gg_figure, "_guard_work_paths", side_effect=AssertionError):
                    result = gg_figure.fetch_direct(root,
                        "https://example.gov/files/article/page.html", [], "s1",
                        rules=[candidate_rule()], list_mode=True)
                self.assertEqual(result["kogl"], expected)
                self.assertEqual(set(result), {"v", "id", "status", "slot", "needsChoice",
                    "images", "kogl", "org", "candidates", "candidatesTruncated"})
                self.assertEqual(get.call_count, 1)
                self.assertEqual(get.call_args.kwargs["kind"], "page")
                self.assertEqual(before, {p.relative_to(root): p.read_bytes()
                    for p in root.rglob("*") if p.is_file()})

    def test_list_url_passes_normal_fetch_and_approval(self):
        for filename in ("a.png", "포스터_vf.png"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp:
                selected = candidate_rule()
                page = "https://example.gov/files/article/page.html"
                html = ("open_mark2.png<img src='/files/" + filename + "'>").encode()
                def transport(url, **kwargs):
                    body = html if kwargs["kind"] == "page" else fixture("nongsaro-image02.jpg")
                    return url, 200, {}, body
                with mock.patch.object(gg_figure, "http_get", side_effect=transport):
                    listed = gg_figure.fetch_direct(temp, page, [], "s1",
                        rules=[selected], list_mode=True)
                    url = listed["candidates"][0]["url"]
                    payload = gg_figure.request_payload("abcdefgh", "t", "s1", page, [url])
                    fetched = gg_figure.fetch_direct(temp, page, payload["images"], "s1",
                        rules=[selected])
                self.assertEqual(fetched["images"][0]["verdict"], "approved")
                self.assertNotIn("candidates", fetched)

    def test_cli_rejects_mixed_and_missing_modes_without_network(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.dict(os.environ, {"KNUAF_GUI": ""}), \
                mock.patch.object(gg_figure, "http_get") as get:
            for extra in ([], ["--list", "--image", IMAGE02]):
                out = io.StringIO()
                with mock.patch("sys.stdout", out):
                    code = gg_figure.main(["fetch", temp, "--slot", "s1", "--page", PAGE_URL] + extra)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out.getvalue())["reason"], "invalid_images")
            get.assert_not_called()


class TestP11Channel(unittest.TestCase):
    setUp = TestChannel.setUp
    beat = TestChannel.beat
    turn = TestChannel.turn

    def fetch(self, *, list_mode=False, sleep=lambda _: None, poll_seconds=0):
        return gg_figure.fetch_app(self.folder, PAGE_URL, [] if list_mode else [IMAGE02],
            "s1", environ={"KNUAF_FIGURE_HOME": str(self.home)}, list_mode=list_mode,
            sleep=sleep, poll_seconds=poll_seconds)

    def test_app_list_reads_candidates_with_selected_local_rule(self):
        self.beat()
        self.turn()
        page = "https://example.gov/files/article/page.html"
        url = "https://example.gov/files/%ED%8F%AC.png"
        def respond(_):
            request = next((self.folder / ".knuaf-gui" / "figure-requests").glob("*.req.json"))
            payload = json.loads(request.read_text(encoding="utf-8"))
            self.assertEqual(payload["images"], [])
            (self.home / "res").mkdir(exist_ok=True)
            (self.home / "res" / (payload["id"] + ".res.json")).write_text(json.dumps(
                list_result(payload["id"], candidates=[{"url": url, "credit": "none"}])))
        with mock.patch.object(gg_figure, "load_rules", return_value=[candidate_rule()]):
            result = gg_figure.fetch_app(self.folder, page, [], "s1", list_mode=True,
                environ={"KNUAF_FIGURE_HOME": str(self.home)}, sleep=respond)
        self.assertEqual(result["candidates"], [{"url": url, "credit": "none"}])
        self.assertEqual(gg_figure.request_payload("abcdefgh", "turn-1", "s1", page,
            [result["candidates"][0]["url"]])["images"], [url])

    def test_turn_change_and_removal_cancel_before_timeout(self):
        self.beat()
        for remove in (False, True):
            with self.subTest(remove=remove):
                self.turn()
                def change(_):
                    path = self.home / "turn.json"
                    if remove:
                        path.unlink()
                    else:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        data["turnId"] = "next-turn"
                        path.write_text(json.dumps(data), encoding="utf-8")
                result = self.fetch(list_mode=True, sleep=change, poll_seconds=60)
                self.assertEqual(result["reason"], "cancelled")

    def test_app_list_request_empty_and_preserves_work(self):
        self.beat()
        self.turn()
        work = self.folder / ".gg-work"
        work.mkdir()
        (work / "keep").write_bytes(b"old result")
        with mock.patch.object(gg_figure, "load_rules", return_value=[rule()]):
            result = self.fetch(list_mode=True)
        self.assertEqual(result["reason"], "timeout")
        request = next((self.folder / ".knuaf-gui" / "figure-requests").glob("*.req.json"))
        self.assertEqual(json.loads(request.read_text(encoding="utf-8"))["images"], [])
        self.assertEqual(list(work.iterdir()), [work / "keep"])
        self.assertEqual((work / "keep").read_bytes(), b"old result")

    def test_backlog_199_accepts_200_rejects_without_deletion(self):
        self.beat()
        self.turn()
        directory = self.folder / ".knuaf-gui" / "figure-requests"
        directory.mkdir(parents=True)
        for n in range(197):
            path = directory / (str(n) + ".req.json")
            path.write_bytes(b"other program old record")
            os.utime(path, (1, 1))
        (directory / "directory.req.json").mkdir()
        (directory / "link.req.json").symlink_to(self.home / "never-open-target")
        before = {p.name: (p.lstat().st_mtime_ns,
            p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in directory.iterdir()}
        self.assertEqual(self.fetch(list_mode=True)["reason"], "timeout")
        self.assertEqual(len(list(directory.iterdir())), 200)
        for list_mode in (False, True):
            with self.subTest(list_mode=list_mode):
                result = self.fetch(list_mode=list_mode)
                self.assertEqual(result["reason"], "request_backlog")
                self.assertIn("200건", result["notice"])
                self.assertEqual(len(list(directory.iterdir())), 200)
        after = {p.name: (p.lstat().st_mtime_ns,
            p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in directory.iterdir() if p.name in before}
        self.assertEqual(before, after)

    def test_backlog_unreadable_directory(self):
        self.beat()
        self.turn()
        with mock.patch.object(gg_figure.os, "scandir", side_effect=PermissionError):
            result = self.fetch(list_mode=True)
        self.assertEqual(result["reason"], "request_backlog")
        self.assertEqual(list((self.folder / ".knuaf-gui" / "figure-requests").iterdir()), [])


class TestP11Slots(unittest.TestCase):
    setUp = TestPlace.setUp
    request_file = TestPlace.request_file
    result_file = TestPlace.result_file
    app_result = TestPlace.app_result
    approved = TestPlace.approved

    def old_success(self):
        self.manuscript.write_text(MANUSCRIPT + "\n![](app-figure:" + "c" * 64 + ")\n",
                                   encoding="utf-8")
        self.result_file(self.app_result([self.approved("a" * 64)]))
        return self.manuscript.read_bytes()

    def place(self):
        return gg_figure.place(self.folder, "s1", "가. 품종 선택", home=self.home, environ={})

    def channel(self):
        (self.home / "figure-channel.json").write_text(json.dumps(
            {"v": 1, "updatedAt": int(time.time() * 1000)}))
        (self.home / "turn.json").write_text(json.dumps(
            {"v": 1, "turnId": "t", "folder": str(self.folder), "startedAt": 0}))

    def test_each_failure_replaces_old_success_before_place(self):
        for failure in ("no_rules", "no_rule", "no_channel", "stale_heartbeat",
                        "timeout", "rejected", "exception"):
            with self.subTest(failure=failure):
                before = self.old_success()
                self.channel()
                env = {"KNUAF_FIGURE_HOME": str(self.home)}
                with mock.patch.object(gg_figure.secrets, "token_hex", return_value="bbbbbbbbbbbbbbbb"):
                    if failure in ("no_rules", "no_rule"):
                        result = gg_figure.fetch_direct(self.folder, PAGE_URL, [IMAGE02], "s1",
                            rules=[] if failure == "no_rules" else [candidate_rule()])
                    else:
                        if failure == "no_channel":
                            env = {}
                        if failure == "stale_heartbeat":
                            (self.home / "figure-channel.json").write_text(json.dumps(
                                {"v": 1, "updatedAt": 0}))
                        if failure == "rejected":
                            (self.home / "res" / "bbbbbbbbbbbbbbbb.res.json").write_text(json.dumps(
                                {"v": 1, "id": "bbbbbbbbbbbbbbbb", "slot": "s1",
                                 "status": "rejected", "reason": "rule_missing", "images": []}))
                        if failure == "exception":
                            with mock.patch.object(gg_figure, "channel_state", side_effect=RuntimeError), \
                                    self.assertRaises(RuntimeError):
                                gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1", environ=env)
                            result = None
                        else:
                            result = gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1",
                                environ=env, poll_seconds=0)
                    if result is not None:
                        self.assertNotEqual(result["status"], "done")
                req = gg_figure._read_request(self.folder, "s1")
                self.assertNotEqual(req["reqId"], "req-0001")
                self.assertEqual(self.manuscript.read_bytes(), before)
                self.assertEqual(self.place()["status"], "needs_photo")
                self.assertNotIn("app-figure:" + "a" * 64, self.manuscript.read_text(encoding="utf-8"))
                self.assertIn("app-figure:" + "c" * 64, self.manuscript.read_text(encoding="utf-8"))
                (self.home / "res" / "bbbbbbbbbbbbbbbb.res.json").unlink(missing_ok=True)

    def test_slot_written_before_channel_rules_request_and_get(self):
        self.old_success()
        self.channel()
        def check(*args, **kwargs):
            self.assertNotEqual(gg_figure._read_request(self.folder, "s1")["reqId"], "req-0001")
        original = gg_figure._write_json_atomic
        def write(path, data):
            if str(path).endswith(".req.json"):
                check()
            return original(path, data)
        with mock.patch.object(gg_figure, "channel_state", side_effect=lambda *a: (check(), (True, ""))[1]), \
                mock.patch.object(gg_figure, "_write_json_atomic", side_effect=write):
            gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1",
                environ={"KNUAF_FIGURE_HOME": str(self.home)}, poll_seconds=0)
        self.old_success()
        def get(*args, **kwargs):
            check()
            raise gg_figure.FigureError("timeout")
        with mock.patch.object(gg_figure, "load_rules", side_effect=lambda *a: (check(), [rule()])[1]), \
                mock.patch.object(gg_figure, "http_get", side_effect=get):
            gg_figure.fetch_direct(self.folder, PAGE_URL, [IMAGE02], "s1")

    def test_write_failure_persists_blocks_place_and_recovery_exposes_known_gap(self):
        before = self.old_success()
        with mock.patch.object(gg_figure, "_write_json_atomic", side_effect=OSError), \
                mock.patch.object(gg_figure, "http_get") as get:
            result = gg_figure.fetch_direct(self.folder, PAGE_URL, [IMAGE02], "s1", rules=[rule()])
            self.assertEqual(result["reason"], "slot_record_failed")
            app_result = gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1",
                environ={"KNUAF_FIGURE_HOME": str(self.home)}, poll_seconds=0)
            self.assertEqual(app_result["reason"], "slot_record_failed")
            self.assertEqual(self.manuscript.read_bytes(), before)
            self.assertEqual(self.place()["status"], "needs_photo")
            get.assert_not_called()
        self.assertFalse((self.folder / ".knuaf-gui").exists())
        self.manuscript.write_bytes(before)
        self.assertEqual(self.place()["status"], "placed")  # r6가 명시한 일시 실패 틈.
        req = gg_figure._read_request(self.folder, "s1")
        self.assertEqual((req["reqId"], req["mode"]), ("req-0001", "app"))
        self.assertIsInstance(req["usedAt"], int)

    def test_slot_directory_and_link_are_treated_as_absent(self):
        manuscript = self.old_success()
        path = gg_figure._request_record_path(self.folder, "s1")
        old = path.read_bytes()
        path.unlink()
        path.mkdir()
        self.assertEqual(self.place()["status"], "needs_photo")
        self.assertIsNone(gg_figure._read_request(self.folder, "s1"))
        self.assertNotIn("app-figure:" + "a" * 64, self.manuscript.read_text(encoding="utf-8"))
        path.rmdir()
        # 각 비정상 기록을 사진 자리 없는 같은 원고에서 독립적으로 검사한다.
        self.manuscript.write_bytes(manuscript)
        target = self.home / "old-slot.json"
        target.write_bytes(old)
        path.symlink_to(target)
        self.assertEqual(self.place()["status"], "needs_photo")
        self.assertEqual(target.read_bytes(), old)
        self.assertIsNone(gg_figure._read_request(self.folder, "s1"))
        text = self.manuscript.read_text(encoding="utf-8")
        self.assertIn("※ 이 자리에 품종 선택 사진 추가 필요", text)
        self.assertNotIn("app-figure:" + "a" * 64, text)
        self.assertIn("app-figure:" + "c" * 64, text)

    def test_done_slot_is_not_rewritten_at_dispatch_end(self):
        self.old_success()
        def get(url, **kwargs):
            body = ('open_mark2.png<img src="' + IMAGE02 + '">').encode() \
                if kwargs["kind"] == "page" else fixture("nongsaro-image02.jpg")
            return url, 200, {}, body
        original = gg_figure._write_request
        with mock.patch.object(gg_figure, "_write_request", wraps=original) as record, \
                mock.patch.object(gg_figure, "load_rules", return_value=[rule()]), \
                mock.patch.object(gg_figure, "http_get", side_effect=get), \
                mock.patch.dict(os.environ, {"KNUAF_GUI": ""}), mock.patch("sys.stdout"):
            code = gg_figure.main(["fetch", str(self.folder), "--slot", "s1", "--page", PAGE_URL,
                                   "--image", IMAGE02])
        self.assertEqual(code, 0)
        self.assertEqual(record.call_count, 1)
        req = gg_figure._read_request(self.folder, "s1")
        self.assertTrue(req["reqId"].startswith("direct-"))
        self.assertNotIn("usedAt", req)
        self.assertEqual(self.place()["status"], "placed")
        self.assertEqual(gg_figure._read_request(self.folder, "s1")["reqId"], req["reqId"])

    def test_lists_all_outcomes_preserve_slot_results_and_manuscript(self):
        for outcome in ("done", "rejected", "timeout", "cancelled"):
            with self.subTest(outcome=outcome):
                before = self.old_success()
                self.channel()
                paths = [gg_figure._request_record_path(self.folder, "s1"),
                         self.home / "res" / "req-0001.res.json"]
                old = [p.read_bytes() for p in paths]
                def result(*args, **kwargs):
                    if outcome == "done":
                        return list_result()
                    if outcome == "rejected":
                        return gg_figure._blocked("rule_missing")
                    return None
                def sleep(_):
                    (self.home / "turn.json").unlink()
                with mock.patch.object(gg_figure, "read_result", side_effect=result):
                    fetched = gg_figure.fetch_app(self.folder, PAGE_URL, [], "s1", list_mode=True,
                        environ={"KNUAF_FIGURE_HOME": str(self.home)},
                        poll_seconds=60 if outcome == "cancelled" else 0, sleep=sleep)
                self.assertEqual(fetched.get("reason", fetched["status"]),
                    {"done": "done", "rejected": "rule_missing", "timeout": "timeout",
                     "cancelled": "cancelled"}[outcome])
                self.assertEqual([p.read_bytes() for p in paths], old)
                self.assertEqual(self.manuscript.read_bytes(), before)
                self.assertEqual(self.place()["status"], "placed")


class TestP11SlotLock(unittest.TestCase):
    setUp = TestPlace.setUp
    request_file = TestPlace.request_file
    result_file = TestPlace.result_file
    app_result = TestPlace.app_result
    approved = TestPlace.approved
    old_success = TestP11Slots.old_success
    place = TestP11Slots.place

    def test_new_fetch_between_initial_read_and_lock_never_restores_old_id(self):
        before = self.old_success()
        acquire = gg_figure._acquire_slot_lock
        latest = {}
        def interleave(folder, slot):
            # 잠금 획득 직전 새 받기를 끝내어 보고된 순서를 결정적으로 고정한다.
            with mock.patch.object(gg_figure, "_acquire_slot_lock", side_effect=acquire):
                latest["result"] = gg_figure.fetch_app(folder, PAGE_URL, [IMAGE02], slot, environ={})
                latest["record"] = gg_figure._read_request(folder, slot)
            return acquire(folder, slot)
        with mock.patch.object(gg_figure, "_acquire_slot_lock", side_effect=interleave):
            result = self.place()
        self.assertEqual(latest["result"]["reason"], "no_channel")
        self.assertNotEqual(latest["record"]["reqId"], "req-0001")
        self.assertEqual(gg_figure._read_request(self.folder, "s1"), latest["record"])
        self.assertEqual(result["status"], "needs_photo")
        self.assertIn("새 요청 결과", result["notice"])
        self.assertNotIn("app-figure:" + "a" * 64, self.manuscript.read_text(encoding="utf-8"))
        self.assertIn("app-figure:" + "c" * 64, self.manuscript.read_text(encoding="utf-8"))
        self.assertNotEqual(self.manuscript.read_bytes(), before)
        self.assertFalse(gg_figure._request_record_path(self.folder, "s1").with_suffix(".json.lock").exists())

    def test_used_at_write_and_insertion_hold_lock_against_competing_fetch(self):
        self.old_success()
        write = gg_figure._write_json_atomic
        competing = []
        def interleave(path, payload):
            if "usedAt" in payload:
                competing.append(gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1", environ={}))
            return write(path, payload)
        with mock.patch.object(gg_figure, "_write_json_atomic", side_effect=interleave), \
                mock.patch.object(gg_figure, "channel_state") as channel:
            result = self.place()
        # place가 먼저 잠금을 잡음: 새 fetch는 선기록/전송 없이 명시 실패한다.
        self.assertEqual(competing[0]["reason"], "slot_record_failed")
        channel.assert_not_called()
        self.assertEqual(result["status"], "placed")
        self.assertEqual(gg_figure._read_request(self.folder, "s1")["reqId"], "req-0001")
        newest = gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1", environ={})
        self.assertEqual(newest["reason"], "no_channel")
        self.assertNotEqual(gg_figure._read_request(self.folder, "s1")["reqId"], "req-0001")

    def test_nine_minute_lock_closes_both_paths_with_retry_notice(self):
        before = self.old_success()
        record = gg_figure._request_record_path(self.folder, "s1")
        lock = record.with_name(record.name + ".lock")
        lock.write_bytes(b"recent owner")
        os.utime(lock, (1000, 1000))
        old_record = record.read_bytes()
        with mock.patch.object(gg_figure.time, "time", return_value=1540), \
                mock.patch.object(gg_figure, "channel_state") as channel:
            fetched = gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1", environ={})
            self.assertEqual(fetched["reason"], "slot_record_failed")
            self.assertIn("잠시 뒤 다시 시도", fetched["notice"])
            self.assertEqual(self.manuscript.read_bytes(), before)
            placed = self.place()
        channel.assert_not_called()
        self.assertEqual(placed["status"], "needs_photo")
        self.assertIn("잠시 뒤 다시 시도", placed["notice"])
        self.assertEqual(record.read_bytes(), old_record)
        self.assertEqual(lock.read_bytes(), b"recent owner")
        self.assertEqual(lock.stat().st_mtime_ns, 1_000_000_000_000)
        self.assertNotIn("app-figure:" + "a" * 64, self.manuscript.read_text(encoding="utf-8"))
        self.assertFalse((self.folder / ".knuaf-gui").exists())

    def test_eleven_minute_lock_is_recovered_for_fetch_and_place(self):
        for action in ("fetch", "place"):
            with self.subTest(action=action):
                self.old_success()
                record = gg_figure._request_record_path(self.folder, "s1")
                lock = record.with_name(record.name + ".lock")
                lock.write_bytes(b"dead owner")
                os.utime(lock, (1000, 1000))
                with mock.patch.object(gg_figure.time, "time", return_value=1660):
                    if action == "fetch":
                        result = gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1", environ={})
                        self.assertEqual(result["reason"], "no_channel")
                        self.assertNotEqual(gg_figure._read_request(self.folder, "s1")["reqId"], "req-0001")
                    else:
                        self.assertEqual(self.place()["status"], "placed")
                self.assertFalse(lock.exists())
                self.assertEqual(list(record.parent.glob("*.stale-*")), [])

    def test_two_stale_recoverers_only_one_acquires(self):
        self.old_success()
        record = gg_figure._request_record_path(self.folder, "s1")
        path = record.with_name(record.name + ".lock")
        path.write_bytes(b"dead owner")
        os.utime(path, (1000, 1000))
        rename = Path.rename
        attempts = []
        held = set()
        def os_lock(fd):
            if held:
                raise BlockingIOError("other reclaimer holds stale inode")
            held.add(fd)
        def race(source, target):
            # 둘 다 stale 이름을 연 시점에 두 번째 회수자를 진입시킨다.
            with self.assertRaises(OSError):
                gg_figure._acquire_slot_lock(self.folder, "s1")
            attempts.append("loser")
            return rename(source, target)
        with mock.patch.object(gg_figure.time, "time", return_value=1660), \
                mock.patch.object(gg_figure.gg_update, "_os_lock", side_effect=os_lock), \
                mock.patch.object(gg_figure.gg_update, "_os_unlock", side_effect=held.remove), \
                mock.patch.object(Path, "rename", autospec=True, side_effect=race) as moved:
            winner = gg_figure._acquire_slot_lock(self.folder, "s1")
        try:
            self.assertEqual(attempts, ["loser"])
            self.assertEqual(moved.call_count, 1)
            self.assertEqual(path.stat().st_ino, os.fstat(winner[1]).st_ino)
            with self.assertRaises(OSError):
                gg_figure._acquire_slot_lock(self.folder, "s1")
        finally:
            gg_figure._release_slot_lock(winner)
        self.assertFalse(path.exists())

    def test_stale_rename_failure_does_not_retry_creation(self):
        self.old_success()
        path = gg_figure._request_record_path(self.folder, "s1").with_suffix(".json.lock")
        path.write_bytes(b"dead owner")
        os.utime(path, (1000, 1000))
        with mock.patch.object(gg_figure.time, "time", return_value=1660), \
                mock.patch.object(Path, "rename", side_effect=FileNotFoundError) as moved, \
                mock.patch.object(gg_figure.os, "open", wraps=os.open) as opened:
            with self.assertRaises(OSError):
                gg_figure._acquire_slot_lock(self.folder, "s1")
        moved.assert_called_once()
        self.assertEqual(sum(bool(call.args[1] & os.O_EXCL) for call in opened.call_args_list), 1)
        self.assertEqual(path.read_bytes(), b"dead owner")

    def test_write_error_releases_lock_and_different_slot_is_independent(self):
        self.old_success()
        lock = gg_figure._acquire_slot_lock(self.folder, "s1")
        try:
            result = gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s2", environ={})
            self.assertEqual(result["reason"], "no_channel")
            self.assertEqual(gg_figure._read_request(self.folder, "s2")["slot"], "s2")
        finally:
            gg_figure._release_slot_lock(lock)
        with mock.patch.object(gg_figure, "_write_json_atomic", side_effect=OSError):
            result = gg_figure.fetch_app(self.folder, PAGE_URL, [IMAGE02], "s1", environ={})
        self.assertEqual(result["reason"], "slot_record_failed")
        self.assertFalse(lock[0].exists())
        self.assertEqual(gg_figure._read_request(self.folder, "s1")["reqId"], "req-0001")


def org_vectors():
    return _vector_expand(json.loads(
        (FIXTURES / "org-vectors.json").read_text(encoding="utf-8")))


def _org_html(html, selected):
    return html.replace("{B}", selected["bodyStart"]).replace("{M}", selected["bodyEnd"])


class TestOrgVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = org_vectors()
        cls.rules, folder = _vector_rules(cls.data)
        cls.addClassCleanup(shutil.rmtree, folder, True)
        cls.selected, = cls.rules

    def load_document(self, document):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            return gg_figure.load_rules(path)

    def test_hit_vectors(self):
        for text, aliases, expected, note in self.data["hit"]:
            with self.subTest(note=note):
                selected = dict(self.selected, orgAliases=gg_figure._sorted_aliases(aliases))
                self.assertEqual(gg_figure._foreign_mark_in(text, selected), expected)

    def test_scope_vectors(self):
        for html, image, expected, note in self.data["scope"]:
            with self.subTest(note=note):
                reading, _ = gg_figure._org_reading(
                    _org_html(html, self.selected), image, self.data["pageUrl"], self.selected)
                self.assertEqual(reading, expected)

    def test_candidate_vectors(self):
        for html, expected, note in self.data["candidate"]:
            with self.subTest(note=note):
                candidates, truncated = gg_figure.extract_candidates(
                    _org_html(html, self.selected), self.data["pageUrl"], self.selected)
                self.assertEqual(candidates, expected)
                self.assertFalse(truncated)

    def test_same_vectors(self):
        for first, second, expected, note in self.data["same"]:
            with self.subTest(note=note):
                self.assertEqual(gg_figure._same_reference(first, second), expected)

    def test_load_vectors(self):
        for top, item, expected, note in self.data["load"]:
            with self.subTest(note=note):
                document = json.loads(json.dumps(self.data["loadBase"]))
                for target, override in ((document, top), (document["rules"][0], item)):
                    for key, value in override.items():
                        if value is None:
                            target.pop(key, None)
                        else:
                            target[key] = value
                self.assertEqual([r["id"] for r in self.load_document(document)], expected)

    def test_load_file_vectors(self):
        for document, expected, note in self.data["loadFile"]:
            with self.subTest(note=note):
                self.assertEqual([r["id"] for r in self.load_document(document)], expected)

    def test_single_symbol_terms_follow_x1_range(self):
        # 한 글자 기관 꼴을 잘못 넓히거나 좁히면 실제 출처 등록이 달라진다.
        for code, expected in ((0x31FF, False), (0x3200, True), (0x3214, True),
                               (0x321C, True), (0x33FF, True), (0x3400, False),
                               (0xAC00, False), (0x41, False)):
            with self.subTest(code=hex(code)):
                document = json.loads(json.dumps(self.data["loadBase"]))
                document["orgTerms"] = [chr(code)]
                ids = [r["id"] for r in self.load_document(document)]
                self.assertEqual(ids, ["a"] if expected else [])

    def test_loader_list_and_marker_limits(self):
        # 공용 벡터 밖의 개수/표지 길이 경계. 범위가 늘면 오승인할 수 있다.
        terms = ["가" + chr(0xAC00 + i) for i in range(301)]
        credits = ["나" + chr(0xAC00 + i) for i in range(41)]
        latin = ["A" + chr(65 + i // 26) + chr(65 + i % 26) for i in range(301)]
        for key, words, maximum in (("orgTerms", terms, 300),
                                    ("creditWords", credits, 40), ("latinOk", latin, 300)):
            for count in (maximum, maximum + 1):
                with self.subTest(key=key, count=count):
                    document = json.loads(json.dumps(self.data["loadBase"]))
                    document[key] = words[:count]
                    ids = [r["id"] for r in self.load_document(document)]
                    self.assertEqual(ids, ["a"] if count == maximum else [])
        for size in (8, 200, 201):
            with self.subTest(marker_size=size):
                document = json.loads(json.dumps(self.data["loadBase"]))
                document["rules"][0]["bodyStart"] = "s" * size
                ids = [r["id"] for r in self.load_document(document)]
                self.assertEqual(ids, ["a"] if size <= 200 else [])

    def test_i9_checks_presence_even_when_null(self):
        for kind in (gg_figure.RULE_PAGE, gg_figure.RULE_PAGE_QUERY):
            for key in ("orgAliases", "bodyStart", "bodyEnd"):
                with self.subTest(kind=kind, key=key):
                    item = dict(self.data["loadBase"]["rules"][0], rule=kind)
                    for field in ("orgAliases", "bodyStart", "bodyEnd"):
                        item.pop(field)
                    if kind == gg_figure.RULE_PAGE:
                        item.pop("pageQuery")
                    item[key] = None
                    self.assertEqual(self.load_document({"v": 1, "rules": [item]}), [])

    @unittest.skip("책임자가 rda 규칙 두 항목을 등록한 뒤 켠다 — 현재 rules: [] 유지")
    def test_real_distribution_rda_aliases_are_fixed(self):
        path = SCRIPTS.parent / "references" / "figure-sources.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        expected = ["rda-infograp", "rda-cardnews"]
        self.assertEqual([r["id"] for r in gg_figure.load_rules(path)], expected)
        for item in raw["rules"]:
            self.assertIn(item["id"], expected)
            self.assertEqual(item["orgAliases"], ["농촌진흥청", "농진청"])


def _org_query_vectors():
    # 주소·쿼리·flow 기대값은 그대로. 시험 기관 표기만 I8 한글꼴로 맞춘다.
    data = _vectors()
    template = org_vectors()["rules"]
    document = data["rules"]
    for key in ("orgTerms", "creditWords", "latinOk"):
        document[key] = template[key]
    for item in document["rules"]:
        if item["rule"] == gg_figure.RULE_PAGE_QUERY:
            item.update(rule=gg_figure.RULE_PAGE_QUERY_ORG, org="시험기관",
                        orgAliases=["시험기관"],
                        bodyStart=template["rules"][0]["bodyStart"],
                        bodyEnd=template["rules"][0]["bodyEnd"])
    return data


class TestOrgQuerySelect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = _org_query_vectors()
        cls.rules, folder = _vector_rules(cls.data)
        cls.addClassCleanup(shutil.rmtree, folder, True)

    test_page_vectors = TestPageQuerySelect.test_page_vectors
    test_image_vectors = TestPageQuerySelect.test_image_vectors


class TestOrgQueryFlow(_ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = _org_query_vectors()
        cls.rules, folder = _vector_rules(cls.data)
        cls.addClassCleanup(shutil.rmtree, folder, True)

    setUp = TestPageQueryFlow.setUp
    path_of = TestPageQueryFlow.path_of
    run_flow = TestPageQueryFlow.run_flow
    test_flow_vectors = TestPageQueryFlow.test_flow_vectors


class TestOrgFetchDirect(_ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = org_vectors()
        cls.rules, folder = _vector_rules(cls.data)
        cls.addClassCleanup(shutil.rmtree, folder, True)
        cls.selected, = cls.rules

    def setUp(self):
        self.server.routes.clear()
        self.server.hits.clear()
        self.folder = Path(tempfile.mkdtemp(prefix="gg-org-"))
        self.addCleanup(shutil.rmtree, self.folder, True)

    path_of = TestPageQueryFlow.path_of

    def fetch(self, html, image="https://www.example.go.kr/files/a.png", *, rules=None):
        self.route(self.path_of(self.data["pageUrl"]), html.encode("utf-8"))
        self.route(self.path_of(image), fixture("nongsaro-image02.jpg"))
        return gg_figure.fetch_direct(
            self.folder, self.data["pageUrl"], [image], "org",
            rules=self.rules if rules is None else rules, resolver=self.resolver,
            allow_loopback=True, tls_context=self.tls)

    def html(self, content, kogl=1):
        return (self.selected["bodyStart"] + content + self.selected["bodyEnd"]
                + " open_mark%d.png" % kogl)

    def assert_not_stored(self, record, reason):
        self.assertEqual(record["verdict"], "ambiguous")
        self.assertEqual(record["reason"], reason)
        self.assertIsNone(record["sha256"])
        self.assertFalse(record.get("path"))
        self.assertFalse((self.folder / "figures").exists())

    def test_bracket_candidate_approves_and_keeps_request_spelling(self):
        html = self.html('<img src="/files/[가]_a.jpg" alt="AI 안내">')
        candidates, _ = gg_figure.extract_candidates(html, self.data["pageUrl"], self.selected)
        url = candidates[0]["url"]
        self.assertEqual(url, "https://www.example.go.kr/files/%5B%EA%B0%80%5D_a.jpg")
        self.route(self.path_of(url), fixture("nongsaro-image02.jpg"))
        for kogl in (1, 2):
            with self.subTest(kogl=kogl):
                # 전송 %XX는 기존대로 대문자화하지만 결과·증명은 요청 표기를 보존한다.
                requested = url.replace("%5B", "%5b").replace("%5D", "%5d")
                result = self.fetch(self.html('<img src="/files/[가]_a.jpg">', kogl), requested)
                record = result["images"][0]
                self.assertEqual(record["verdict"], "approved")
                self.assertEqual(record["kogl"], kogl)
                self.assertEqual(record["url"], requested)
                self.assertEqual(record["finalUrl"], requested)
                self.assertEqual((self.folder / record["path"]).read_bytes(),
                                 fixture("nongsaro-image02.jpg"))
                self.assertEqual(self.server.hits[-1],
                                 ("www.example.go.kr", self.path_of(url)))
                receipt = json.loads((self.folder / ".gg-work" / "figure-receipts.jsonl")
                                     .read_text("utf-8").splitlines()[-1])
                self.assertEqual(receipt["image"]["url"], requested)
                self.assertEqual(receipt["image"]["finalUrl"], requested)

    def test_foreign_mark_stops_before_store_and_kogl(self):
        record = self.fetch(self.html('<img src="/files/a.png" alt="FAO 제공">', 4))["images"][0]
        self.assert_not_stored(record, "third_party_org")

    def test_missing_body_is_unknown(self):
        record = self.fetch('open_mark1.png<img src="/files/a.png">')["images"][0]
        self.assert_not_stored(record, "third_party_unknown")

    def test_redirect_final_image_must_be_on_page(self):
        page = self.data["pageUrl"]
        self.route(self.path_of(page), self.html('<img src="/files/a.png">').encode())
        self.route("/files/a.png", status=302, headers={"location": "/files/b.png"})
        self.route("/files/b.png", fixture("nongsaro-image02.jpg"))
        result = gg_figure.fetch_direct(
            self.folder, page, ["https://www.example.go.kr/files/a.png"], "org",
            rules=self.rules, resolver=self.resolver, allow_loopback=True, tls_context=self.tls)
        record = result["images"][0]
        self.assert_not_stored(record, "not_on_page")
        self.assertEqual(record["finalUrl"], "https://www.example.go.kr/files/b.png")

    def test_body_wide_explicit_credit_keeps_existing_reason(self):
        html = self.html('<img src="/files/a.png">' + "x" * 1000 + "©")
        record = self.fetch(html)["images"][0]
        self.assert_not_stored(record, "third_party_mark:©")
        candidates, _ = gg_figure.extract_candidates(html, self.data["pageUrl"], self.selected)
        self.assertEqual(candidates[0]["credit"], "credit")

    def test_unknown_scope_precedes_body_foreign_mark(self):
        html = '<img src="/files/a.png">' + self.html("FAO 제공")
        self.assert_not_stored(self.fetch(html)["images"][0], "third_party_unknown")

    def test_query_page_redirect_is_not_followed(self):
        page = self.data["pageUrl"]
        self.route(self.path_of(page), status=302,
                   headers={"location": "/board/board.do?k=v"})
        result = gg_figure.fetch_direct(
            self.folder, page, ["https://www.example.go.kr/files/a.png"], "org",
            rules=self.rules, resolver=self.resolver, allow_loopback=True, tls_context=self.tls)
        self.assertEqual(result["reason"], "redirect_not_allowed")
        self.assertEqual(self.server.hits, [("www.example.go.kr", self.path_of(page))])

    def test_reference_equivalence_does_not_widen_image_path(self):
        selected = dict(self.selected, imagePath=re.compile(r"^/files/%5Bx%5D\.png$"))
        requested = "https://www.example.go.kr/files/%5bx%5d.png"
        result = self.fetch(self.html('<img src="/files/[x].png">'), requested,
                            rules=[selected])
        self.assertEqual(result["images"][0]["reason"], "path_not_allowed")
        self.assertEqual(self.server.hits, [("www.example.go.kr", self.path_of(self.data["pageUrl"]))])


if __name__ == "__main__":
    unittest.main()
