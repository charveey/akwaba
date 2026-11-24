"""Lot 11a — tests de la configuration Nginx réelle (deploy/nginx/) devant un faux backend.

Prérequis : binaire `nginx` (>= 1.24) dans le PATH. Lancement :

    python3 -m pytest tests/nginx -q

La configuration est copiée telle quelle ; seuls changent, pour le test :
les chemins absolus (/etc/nginx, /usr/share/nginx/akwaba, /tmp), le port
d'écoute, l'adresse du backend (snippets/upstream.conf) et l'adresse du
proxy de confiance (10.250.11.1 → 127.0.0.1, la passerelle simulée).
"""
import http.client
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NGINX_DIR = ROOT / "deploy" / "nginx"
FRONTEND = ROOT / "frontend"
NGINX = shutil.which("nginx") or ("/usr/sbin/nginx" if os.path.exists("/usr/sbin/nginx") else None)

pytestmark = pytest.mark.skipif(NGINX is None, reason="binaire nginx absent")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend(BaseHTTPRequestHandler):
    """Renvoie en JSON ce que le backend reçoit réellement."""

    def _reply(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        payload = json.dumps({
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body.decode(errors="replace"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Powered-By", "fuite")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _reply

    def log_message(self, *args) -> None:
        pass


@pytest.fixture(scope="module")
def nginx(tmp_path_factory):
    work = tmp_path_factory.mktemp("nginx")
    backend_port, port = free_port(), free_port()
    server = ThreadingHTTPServer(("127.0.0.1", backend_port), Backend)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    conf = work / "etc"
    shutil.copytree(NGINX_DIR, conf, ignore=shutil.ignore_patterns(".gitkeep"))
    shutil.copy("/etc/nginx/mime.types", conf / "mime.types")
    (conf / "snippets" / "upstream.conf").write_text(f'set $akwaba_backend "http://127.0.0.1:{backend_port}";\n')
    tmp = work / "tmp"
    tmp.mkdir()
    for path in [conf / "nginx.conf", *conf.glob("conf.d/*.conf"), *conf.glob("snippets/*.conf")]:
        text = path.read_text()
        text = text.replace("/tmp/", f"{tmp}/")  # en premier : `work` est lui-même sous /tmp
        text = text.replace("/etc/nginx/", f"{conf}/").replace("/usr/share/nginx/akwaba", str(FRONTEND))
        text = text.replace("listen 8080", f"listen 127.0.0.1:{port}")
        text = text.replace("set_real_ip_from 10.250.11.1;", "set_real_ip_from 127.0.0.1;")
        text = text.replace("access_log /dev/stdout akwaba;", f"access_log {tmp}/access.log akwaba;")
        text = text.replace("error_log stderr warn;", f"error_log {tmp}/error.log warn;")
        path.write_text(text)

    cmd = [NGINX, "-p", str(work), "-c", str(conf / "nginx.conf"), "-e", str(tmp / "error.log")]
    check = subprocess.run([*cmd, "-t"], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    proc = subprocess.Popen([*cmd, "-g", "daemon off;"], stderr=subprocess.PIPE)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield {"port": port, "tmp": tmp}
    proc.send_signal(signal.SIGQUIT)
    proc.wait(timeout=10)
    server.shutdown()


def req(nginx, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", nginx["port"], timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


def backend_saw(nginx, path, headers=None, method="GET", body=None):
    status, _, data = req(nginx, method, path, headers, body)
    assert status == 200, (status, data)
    return json.loads(data)


# --- configuration -------------------------------------------------------------

def test_nginx_config_is_valid(nginx):
    assert nginx["port"]


# --- routage -------------------------------------------------------------------

def test_root_serves_index(nginx):
    status, headers, body = req(nginx, "GET", "/")
    assert status == 200
    assert b'<html lang="fr"' in body
    assert headers["content-type"].startswith("text/html")
    assert headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path, ctype", [
    ("/assets/js/app.js", "javascript"),
    ("/assets/css/main.css", "text/css"),
    ("/assets/favicon.svg", "image/svg+xml"),
])
def test_static_assets(nginx, path, ctype):
    status, headers, _ = req(nginx, "GET", path)
    assert status == 200
    assert ctype in headers["content-type"]
    assert headers["cache-control"] == "no-cache"
    assert "etag" in headers


@pytest.mark.parametrize("path", [
    "/index.html", "/membres", "/README.md", "/frontend/README.md", "/tests/", "/.env", "/.git/config",
    "/assets/", "/assets/js/", "/assets/css", "/assets/js/app.js.map", "/assets/../index.html",
    "/assets/js/inexistant.js", "/docker-compose.yml", "/etc/passwd", "/API/members",
])
def test_unknown_or_internal_paths_are_404(nginx, path):
    status, _, body = req(nginx, "GET", path)
    assert status == 404, path
    assert b"Index of" not in body


@pytest.mark.parametrize("path", [
    "/api/agent", "/api/agent/", "/api/agent/ping", "/api/agent/batches", "/api/agent/heartbeat",
    "/api//agent/ping", "/api/./agent/ping", "/api/x/../agent/ping", "/api/%61gent/ping",
    "/api/agent/ping?x=1", "/api/agent%2fping",
])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_agent_api_is_blocked(nginx, path, method):
    status, _, body = req(nginx, method, path, body=b"{}" if method == "POST" else None)
    assert status == 404, (method, path)
    assert b'"path"' not in body  # n'a jamais atteint le backend


def test_api_is_proxied_with_path_and_query(nginx):
    seen = backend_saw(nginx, "/api/members?page=2&q=%C3%A9")
    assert seen["path"] == "/api/members?page=2&q=%C3%A9"


def test_api_mutation_is_proxied_with_body_and_auth_headers(nginx):
    seen = backend_saw(nginx, "/api/members", method="POST", body=b'{"a":1}', headers={
        "Content-Type": "application/json", "Cookie": "akwaba_session=abc", "X-CSRF-Token": "tok",
        "Origin": "https://ubuntu.tail14614.ts.net:8443", "Sec-Fetch-Site": "same-origin",
        "Referer": "https://ubuntu.tail14614.ts.net:8443/",
    })
    h = seen["headers"]
    assert seen["body"] == '{"a":1}'
    assert h["cookie"] == "akwaba_session=abc"
    assert h["x-csrf-token"] == "tok"
    assert h["origin"] == "https://ubuntu.tail14614.ts.net:8443"
    assert h["sec-fetch-site"] == "same-origin"


def test_agent_like_prefix_still_proxied(nginx):
    assert backend_saw(nginx, "/api/agentx")["path"] == "/api/agentx"


def test_static_rejects_post(nginx):
    assert req(nginx, "POST", "/")[0] in (403, 405)
    assert req(nginx, "POST", "/assets/js/app.js")[0] in (403, 405)


def test_body_size_limit(nginx):
    assert req(nginx, "POST", "/api/members", body=b"x" * (1024 * 1024 + 10))[0] == 413


# --- en-têtes proxy --------------------------------------------------------------

def test_real_ip_takes_only_last_forwarded_entry(nginx):
    """Depuis la passerelle de confiance : seule la dernière entrée (ajoutée par tailscaled) compte."""
    h = backend_saw(nginx, "/api/members", headers={
        "X-Forwarded-For": "6.6.6.6, 100.101.102.103",
        "X-Real-IP": "7.7.7.7",
        "X-Forwarded-Proto": "http",
        "X-Forwarded-Host": "evil.example",
        "X-Forwarded-Port": "1",
        "Forwarded": "for=8.8.8.8",
        "Tailscale-User-Login": "admin@example.com",
        "Tailscale-User-Name": "Admin",
        "Tailscale-App-Capabilities": "{}",
    })["headers"]
    assert h["x-real-ip"] == "100.101.102.103"
    assert h["x-forwarded-for"] == "100.101.102.103"        # écrasé, pas complété
    assert h["x-forwarded-proto"] == "https"
    for gone in ("x-forwarded-host", "x-forwarded-port", "forwarded", "tailscale-user-login", "tailscale-user-name", "tailscale-app-capabilities"):
        assert gone not in h, gone


def test_without_forwarded_for_peer_address_is_used(nginx):
    h = backend_saw(nginx, "/api/members")["headers"]
    assert h["x-real-ip"] == "127.0.0.1"
    assert h["x-forwarded-for"] == "127.0.0.1"


def test_invalid_forwarded_for_is_not_trusted(nginx):
    h = backend_saw(nginx, "/api/members", headers={"X-Forwarded-For": "pas-une-ip"})["headers"]
    assert h["x-real-ip"] == "127.0.0.1"


def test_untrusted_peer_cannot_set_real_ip():
    """Seule la passerelle Docker est déclarée de confiance dans la configuration livrée."""
    conf = (NGINX_DIR / "conf.d" / "akwaba.conf").read_text()
    trusted = re.findall(r"^\s*set_real_ip_from\s+(\S+);", conf, re.M)
    assert trusted == ["10.250.11.1"]
    assert re.search(r"^\s*real_ip_recursive\s+off;", conf, re.M)


# --- en-têtes de sécurité ----------------------------------------------------------

EXPECTED_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; "
    "require-trusted-types-for 'script'"
)


@pytest.mark.parametrize("path", ["/", "/assets/js/app.js", "/assets/css/main.css"])
def test_security_headers_on_pages(nginx, path):
    _, h, _ = req(nginx, "GET", path)
    assert h["content-security-policy"] == EXPECTED_CSP
    assert "unsafe-inline" not in h["content-security-policy"]
    assert "unsafe-eval" not in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["x-frame-options"] == "DENY"
    assert "camera=()" in h["permissions-policy"] and "geolocation=()" in h["permissions-policy"]
    assert "strict-transport-security" not in h      # HSTS toucherait l'hôte du Funnel
    assert h["server"] == "nginx"                     # sans numéro de version


def test_security_headers_on_api(nginx):
    _, h, _ = req(nginx, "GET", "/api/members")
    assert h["cache-control"] == "no-store"           # celui du backend est remplacé
    assert h["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert "x-powered-by" not in h


def test_security_headers_on_404(nginx):
    _, h, _ = req(nginx, "GET", "/introuvable")
    assert h["server"] == "nginx"


def test_healthz_only_from_container_loopback(nginx):
    assert req(nginx, "GET", "/healthz")[0] == 200
    # Via tailscale serve (IP tailnet reconstituée) : invisible.
    assert req(nginx, "GET", "/healthz", headers={"X-Forwarded-For": "100.101.102.103"})[0] == 404


# --- divers ------------------------------------------------------------------------

def test_login_rate_limited(nginx):
    codes = [req(nginx, "POST", "/api/auth/login", body=b"{}")[0] for _ in range(12)]
    assert codes[0] == 200
    assert 429 in codes


def test_access_log_has_no_query_nor_cookie(nginx):
    req(nginx, "GET", "/api/members?secret=xyz", headers={"Cookie": "akwaba_session=SECRET"})
    log = (nginx["tmp"] / "access.log").read_text()
    assert "xyz" not in log and "SECRET" not in log
