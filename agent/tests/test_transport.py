import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from akwaba_agent.sender.client import HttpTransport


class Handler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def _reply(self, code, body=b"{}", extra=None):
        self.send_response(code)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Handler.seen.append((self.path, body, dict(self.headers)))
        try:
            if self.path == "/ok":
                self._reply(200, b'{"status":"ok"}')
            elif self.path == "/bad":
                self._reply(422, b'{"detail":"x"}')
            elif self.path == "/redirect":
                self._reply(302, b"", {"Location": "/ok"})
            elif self.path == "/slow":
                time.sleep(1.0)
                self._reply(200)
            else:
                self._reply(404)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_success_returns_status_body_and_date(server):
    r = HttpTransport(server).post("/ok", b'{"a":1}', {"X-Akwaba-Agent": "agt_x"})
    assert r.status == 200 and r.body == b'{"status":"ok"}' and r.date and r.category is None


def test_headers_and_body_arrive_unchanged(server):
    Handler.seen.clear()
    HttpTransport(server).post("/ok", b'{"a":1}', {"X-Akwaba-Agent": "agt_x", "X-Akwaba-Nonce": "ab" * 16})
    path, body, headers = Handler.seen[-1]
    assert path == "/ok" and body == b'{"a":1}'
    assert headers["X-Akwaba-Agent"] == "agt_x" and headers["Content-Type"] == "application/json"


def test_an_http_error_is_a_reply_not_an_exception(server):
    r = HttpTransport(server).post("/bad", b"{}", {})
    assert r.status == 422 and r.date
    assert HttpTransport(server).post("/inconnu", b"{}", {}).status == 404


def test_redirects_are_not_followed(server):
    Handler.seen.clear()
    r = HttpTransport(server).post("/redirect", b"{}", {"X-Akwaba-Signature": "s"})
    assert r.status == 302 and [p for p, _, _ in Handler.seen] == ["/redirect"]


def test_connection_refused_is_a_network_failure():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = HttpTransport("http://127.0.0.1:%d" % port, timeout=2).post("/ok", b"{}", {})
    assert (r.status, r.category) == (0, "network")


def test_a_slow_server_is_a_timeout(server):
    r = HttpTransport(server, timeout=0.3).post("/slow", b"{}", {})
    assert (r.status, r.category) == (0, "timeout")


def test_environment_proxies_are_ignored(server, monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert HttpTransport(server).post("/ok", b"{}", {}).status == 200


@pytest.mark.parametrize("url", ["", "x", "ftp://h:1", "http://h:1/prefix", "http://h:1?a=1", "http:///x"])
def test_invalid_backend_urls_are_refused(url):
    with pytest.raises(ValueError):
        HttpTransport(url)
