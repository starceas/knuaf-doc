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
             "-addext", "subjectAltName=DNS:" + HOST],
            check=True, capture_output=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.server.routes = {}
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

    def test_rejected_new_request_clears_the_old_slot_record(self):
        """I-3: 새 요청이 거절되면 옛 reqId 기록을 지워 옛 그림이 들어가지 않는다."""
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
        self.assertIsNone(gg_figure._read_request(self.folder, "s1"))

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
        self.assertEqual(result["reason"], "store_symlink")
        self.assertEqual(result["detail"], ".gg-work")
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


if __name__ == "__main__":
    unittest.main()
