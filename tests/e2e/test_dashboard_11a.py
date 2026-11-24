"""Lot 11a — bout en bout dans un vrai Chrome :

    Chrome ──HTTPS──► terminaison TLS (simule tailscale serve :8443, ajoute X-Forwarded-For)
           ──HTTP──► Nginx (configuration deploy/nginx réelle)
           ──HTTP──► backend FastAPI (uvicorn, vrai code) ──► PostgreSQL (migrations Alembic)

Prérequis (sinon test ignoré) : TEST_DATABASE_URL, nginx, google-chrome (ou chromium), node >= 22, openssl.

    TEST_DATABASE_URL=postgresql+psycopg://u:p@127.0.0.1:5432/akwaba_test python3 -m pytest tests/e2e -q
"""
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
URL = os.environ.get("TEST_DATABASE_URL")
NGINX = shutil.which("nginx") or ("/usr/sbin/nginx" if os.path.exists("/usr/sbin/nginx") else None)
CHROME = next((shutil.which(b) for b in ("google-chrome", "chromium", "chromium-browser") if shutil.which(b)), None)
NODE = shutil.which("node")
TAILNET_CLIENT_IP = "100.64.0.7"   # IP que tailscaled ajouterait dans X-Forwarded-For

pytestmark = pytest.mark.skipif(
    not (URL and NGINX and CHROME and NODE and shutil.which("openssl")),
    reason="TEST_DATABASE_URL, nginx, chrome, node ou openssl absent",
)

sys.path.insert(0, str(BACKEND))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"port {port} injoignable")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    from sqlalchemy import create_engine, text
    from app.core.security import hash_password

    work = tmp_path_factory.mktemp("e2e")
    tls_port, nginx_port, backend_port = free_port(), free_port(), free_port()
    tls_origin = f"https://localhost:{tls_port}"
    procs = []

    tls_origin_env = {
        "SECRET_KEY": "e" * 48,
        "FIELD_ENCRYPTION_KEY": "A" * 43 + "=",
        "ALLOWED_ORIGINS": tls_origin,
    }
    for k, v in tls_origin_env.items():
        os.environ.setdefault(k, v)   # app.core.security et alembic chargent la configuration
    env = {**os.environ, **tls_origin_env, "DATABASE_URL": URL, "PYTHONPATH": str(BACKEND)}
    subprocess.run(["alembic", "downgrade", "base"], cwd=BACKEND, env=env, check=True, capture_output=True)
    subprocess.run(["alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)
    engine = create_engine(URL)
    email, password = f"{uuid.uuid4().hex[:8]}@example.test", "mot-de-passe-solide-e2e"
    with engine.begin() as c:
        c.execute(text("INSERT INTO admin_users (email, password_hash, is_active, display_name) "
                       "VALUES (:e, :h, true, 'Admin Test')"), {"e": email, "h": hash_password(password)})

    # Backend réel.
    backend_env = {
        **env,
        "TRUSTED_PROXY_IPS": "127.0.0.1",      # = l'IP fixe de Nginx en production
        "COOKIE_SECURE": "true",
        "LOG_LEVEL": "WARNING",
    }
    procs.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port)],
        cwd=BACKEND, env=backend_env, stdout=open(work / "backend.log", "w"), stderr=subprocess.STDOUT))

    # Nginx : configuration livrée + un serveur TLS qui joue le rôle de tailscale serve.
    conf, tmp = work / "etc", work / "tmp"
    shutil.copytree(ROOT / "deploy" / "nginx", conf, ignore=shutil.ignore_patterns(".gitkeep"))
    shutil.copy("/etc/nginx/mime.types", conf / "mime.types")
    tmp.mkdir()
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                    "-keyout", str(conf / "key.pem"), "-out", str(conf / "cert.pem")], check=True, capture_output=True)
    (conf / "snippets" / "upstream.conf").write_text(f'set $akwaba_backend "http://127.0.0.1:{backend_port}";\n')
    for path in [conf / "nginx.conf", *conf.glob("conf.d/*.conf"), *conf.glob("snippets/*.conf")]:
        t = path.read_text().replace("/tmp/", f"{tmp}/")
        t = t.replace("/etc/nginx/", f"{conf}/").replace("/usr/share/nginx/akwaba", str(ROOT / "frontend"))
        t = t.replace("listen 8080", f"listen 127.0.0.1:{nginx_port}")
        t = t.replace("set_real_ip_from 10.250.11.1;", "set_real_ip_from 127.0.0.1;")
        t = t.replace("access_log /dev/stdout akwaba;", f"access_log {tmp}/access.log akwaba;")
        t = t.replace("error_log stderr warn;", f"error_log {tmp}/error.log warn;")
        path.write_text(t)
    # Écrit après la réécriture des chemins (il contient déjà des chemins absolus).
    (conf / "conf.d" / "tailscale-serve-sim.conf").write_text(f"""
server {{
    listen 127.0.0.1:{tls_port} ssl;
    ssl_certificate {conf}/cert.pem;
    ssl_certificate_key {conf}/key.pem;
    location / {{
        proxy_pass http://127.0.0.1:{nginx_port};
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For "{TAILNET_CLIENT_IP}";
    }}
}}
""")
    main_conf = conf / "nginx.conf"
    main_conf.write_text(main_conf.read_text().replace(
        f"include {conf}/conf.d/akwaba.conf;", f"include {conf}/conf.d/*.conf;"))
    procs.append(subprocess.Popen([NGINX, "-p", str(work), "-c", str(main_conf), "-e", str(tmp / "error.log"),
                                   "-g", "daemon off;"]))
    for p in (backend_port, nginx_port, tls_port):
        wait_port(p)

    # Chrome headless.
    profile = work / "chrome"
    chrome = subprocess.Popen([
        CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--ignore-certificate-errors",
        "--no-first-run", "--no-default-browser-check", f"--user-data-dir={profile}",
        "--remote-debugging-port=0", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    procs.append(chrome)
    devtools_port = None
    end = time.time() + 20
    while time.time() < end and devtools_port is None:
        line = chrome.stderr.readline()
        if "DevTools listening on ws://" in line:
            devtools_port = int(line.split("ws://127.0.0.1:")[1].split("/")[0])
    assert devtools_port, "Chrome n'a pas démarré"

    yield {"engine": engine, "email": email, "password": password, "base": tls_origin,
           "devtools": devtools_port, "work": work, "tmp": tmp}

    for p in reversed(procs):
        p.send_signal(signal.SIGTERM if p is not procs[1] else signal.SIGQUIT)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    engine.dispose()
    subprocess.run(["alembic", "downgrade", "base"], cwd=BACKEND, env=env, capture_output=True)


def test_browser_journey(stack):
    from sqlalchemy import text

    proc = subprocess.Popen(
        [NODE, str(ROOT / "tests" / "e2e" / "browser_11a.mjs"), str(stack["devtools"]), stack["base"],
         stack["email"], stack["password"]],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    outcome = None
    for line in proc.stdout:
        if line.startswith("ATTENTE:revoquer"):
            with stack["engine"].begin() as c:
                c.execute(text("UPDATE admin_sessions SET revoked_at = now() WHERE revoked_at IS NULL"))
            proc.stdin.write("ok\n")
            proc.stdin.flush()
        elif line.startswith("RESULTAT:"):
            outcome = json.loads(line[len("RESULTAT:"):])
    proc.wait(timeout=120)
    assert outcome, "aucun résultat du scénario navigateur"
    (stack["work"] / "browser-results.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
    failed = [f"{r['name']} — {r['detail']}" for r in outcome["results"] if not r["ok"]]
    for r in outcome["results"]:
        print(("OK   " if r["ok"] else "ÉCHEC"), r["name"])
    assert not failed, "\n".join(failed)


def test_audit_records_real_tailnet_ip(stack):
    """L'IP ajoutée par tailscaled traverse Nginx (realip) et le backend (TRUSTED_PROXY_IPS)."""
    from sqlalchemy import text
    with stack["engine"].connect() as c:
        ips = c.execute(text(
            "SELECT DISTINCT ip_address FROM audit_logs WHERE action LIKE 'admin.login%'"
        )).scalars().all()
    assert ips == [TAILNET_CLIENT_IP], ips


def test_nginx_access_log_has_no_secrets(stack):
    log = (stack["tmp"] / "access.log").read_text()
    assert "akwaba_session" not in log and stack["password"] not in log
    assert TAILNET_CLIENT_IP in log
