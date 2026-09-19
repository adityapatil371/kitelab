"""The door in front of the dashboard, and the gzip behind it.

Hermetic: every server here binds loopback on an ephemeral port and serves a
temporary payload, so nothing reads /data or reaches the network.

WHY THESE ARE TESTS RATHER THAN A RUNBOOK NOTE. Both interlocks are refusals,
and a refusal that quietly stops refusing looks exactly like everything working.
The 403 one especially: it fires only on a request carrying proxy headers, which
is a request nobody makes by hand and a browser never makes at all, so the only
way it gets exercised is here.
"""
from __future__ import annotations

import gzip
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from kitelab import access, dashboard_server as ds

PASS = "harbour-ingot-jetty-kernel"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):     # a 303 is the answer, not a step
        return None


def get(port, path, headers=None, data=None, cookie=None):
    """One request. Returns (status, headers, body) and never follows a redirect."""
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = cookie
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=h)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class DoorCase(unittest.TestCase):
    """A guarded and an unguarded server, both over a payload we control."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.payload = json.dumps({"pad": "x" * 200_000}).encode()
        data = Path(cls._tmp.name) / "dashboard.json"
        data.write_bytes(cls.payload)
        cls._real_data, ds.DATA_PATH = ds.DATA_PATH, data
        ds._GZIP_CACHE.clear()
        cls.guarded, cls.gport = cls._start(PASS)
        cls.open_, cls.oport = cls._start(None)

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.guarded, cls.open_):
            srv.shutdown()
            srv.server_close()
        ds.DATA_PATH = cls._real_data
        ds._GZIP_CACHE.clear()
        cls._tmp.cleanup()

    @classmethod
    def _start(cls, passphrase):
        handler = type("H", (ds.Handler,), {"gate": access.Gate(passphrase)})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, srv.server_address[1]

    def open_sesame(self, port=None):
        port = port or self.gport
        _, head, _ = get(port, "/login", data=f"passphrase={PASS}".encode())
        return head["Set-Cookie"].split(";")[0]


class TestServeRefusesToExpose(unittest.TestCase):
    """The bind-address interlock. It has no off switch, and that is the point."""

    def test_non_loopback_without_a_passphrase_refuses(self):
        with self.assertRaises(SystemExit) as caught:
            ds.serve(9, "0.0.0.0", None)
        self.assertIn("without a passphrase", str(caught.exception))

    def test_non_loopback_with_a_short_passphrase_refuses(self):
        with self.assertRaises(SystemExit) as caught:
            ds.serve(9, "0.0.0.0", "short")
        self.assertIn("minimum", str(caught.exception))

    def test_a_real_passphrase_has_no_problem(self):
        self.assertIsNone(access.Gate(PASS).problem())


class TestGuarded(DoorCase):
    def test_the_page_is_the_door_until_you_are_let_in(self):
        status, _, body = get(self.gport, "/")
        self.assertEqual(status, 200)
        self.assertIn(b'name="passphrase"', body)

    def test_an_unauthenticated_poll_gets_401_json_not_html(self):
        # The page distinguishes "session expired" from "server died" on this,
        # and sends the viewer back to /login rather than showing a parse error.
        status, _, body = get(self.gport, "/api/dashboard")
        self.assertEqual(status, 401)
        self.assertTrue(json.loads(body)["locked"])

    def test_a_wrong_passphrase_says_so_and_admits_nobody(self):
        status, head, body = get(self.gport, "/login", data=b"passphrase=nope")
        self.assertEqual(status, 200)
        self.assertIn(b'class="err"', body)
        self.assertNotIn("Set-Cookie", head)

    def test_the_right_passphrase_sets_a_defended_cookie(self):
        status, head, _ = get(self.gport, "/login",
                              data=f"passphrase={PASS}".encode())
        self.assertEqual(status, 303)
        cookie = head["Set-Cookie"]
        self.assertTrue(cookie.startswith(f"{access.COOKIE}="))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_with_the_cookie_the_dashboard_is_served(self):
        status, _, body = get(self.gport, "/", cookie=self.open_sesame())
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Kitelab", body[:4000])

    def test_logout_kills_the_session(self):
        cookie = self.open_sesame()
        status, head, _ = get(self.gport, "/logout", cookie=cookie)
        self.assertEqual(status, 303)
        self.assertEqual(head["Location"], "/login")
        self.assertEqual(get(self.gport, "/api/dashboard", cookie=cookie)[0], 401)


class TestTheTunnelInterlock(DoorCase):
    """The 403 that catches what the bind check cannot see.

    `cloudflared` runs on this machine and connects to 127.0.0.1, so an
    unguarded dashboard on the open internet still looks like a private loopback
    one to `serve()`. The forwarding headers are the only signal that it is not.
    """

    def test_an_ordinary_request_to_an_unguarded_server_is_served(self):
        status, _, body = get(self.oport, "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Kitelab", body[:4000])

    def test_a_proxied_request_to_an_unguarded_server_is_refused(self):
        for header in ("X-Forwarded-For", "CF-Connecting-IP", "X-Forwarded-Proto"):
            for route in ("/", "/api/dashboard", "/api/status"):
                with self.subTest(header=header, route=route):
                    status, _, body = get(self.oport, route, {header: "1.2.3.4"})
                    self.assertEqual(status, 403)
                    self.assertIn(b"not serving", body)

    def test_a_proxied_request_to_a_GUARDED_server_is_fine(self):
        # This is the ordinary case once a passphrase is set: every request
        # through the tunnel carries these headers.
        status, _, _ = get(self.gport, "/", {"X-Forwarded-For": "1.2.3.4"})
        self.assertEqual(status, 200)


class TestThrottle(DoorCase):
    def test_too_many_wrong_tries_shuts_the_door_on_the_right_one(self):
        srv, port = self._start(PASS)
        try:
            for _ in range(access.MAX_FAILS_PER_CLIENT + 1):
                get(port, "/login", data=b"passphrase=nope")
            status, head, body = get(port, "/login",
                                     data=f"passphrase={PASS}".encode())
            self.assertEqual(status, 200)           # the door, not a 303
            self.assertIn(b"Too many wrong tries", body)
            self.assertNotIn("Set-Cookie", head)
        finally:
            srv.shutdown()
            srv.server_close()


class TestGzip(DoorCase):
    def test_the_payload_is_compressed_and_identical(self):
        cookie = self.open_sesame()
        status, head, body = get(self.gport, "/api/dashboard",
                                 {"Accept-Encoding": "gzip"}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(head["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(body), self.payload)
        self.assertLess(len(body), len(self.payload) // 4)

    def test_a_client_that_does_not_ask_gets_plain_bytes(self):
        _, head, body = get(self.gport, "/api/dashboard", cookie=self.open_sesame())
        self.assertNotIn("Content-Encoding", head)
        self.assertEqual(body, self.payload)

    def test_a_small_body_is_not_compressed(self):
        # Below MIN_GZIP the header costs more than the compression saves.
        _, head, _ = get(self.gport, "/api/status", {"Accept-Encoding": "gzip"},
                         cookie=self.open_sesame())
        self.assertNotIn("Content-Encoding", head)

    def test_a_rebuilt_payload_is_not_served_from_the_old_cache(self):
        """The memo is keyed on (size, mtime), not on the path."""
        cookie = self.open_sesame()
        get(self.gport, "/api/dashboard", {"Accept-Encoding": "gzip"}, cookie=cookie)
        fresh = json.dumps({"pad": "y" * 200_001}).encode()
        ds.DATA_PATH.write_bytes(fresh)
        _, _, body = get(self.gport, "/api/dashboard",
                         {"Accept-Encoding": "gzip"}, cookie=cookie)
        self.assertEqual(gzip.decompress(body), fresh)
        ds.DATA_PATH.write_bytes(self.payload)


if __name__ == "__main__":
    unittest.main()
