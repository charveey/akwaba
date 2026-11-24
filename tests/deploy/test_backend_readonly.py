"""Lot 13a — le backend fonctionne sur une racine en LECTURE SEULE (read_only: true dans docker-compose.yml).

Docker n'étant pas disponible partout, on reproduit les conditions du conteneur :
  - utilisateur sans privilège (nobody, comme uid 10001 dans l'image), aucun répertoire personnel ;
  - copie de /srv (alembic.ini, alembic/, app/) en lecture seule, sans cache Python ;
  - seul /tmp est inscriptible (tmpfs dans le conteneur) ; options uvicorn lues dans le Dockerfile.
On y exécute les migrations, `alembic check`, puis un parcours HTTP qui écrit en base (connexion,
création de membre, export CSV, déconnexion). Aucun fichier ne doit apparaître dans /srv.
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
NOBODY = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups"]

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL") or shutil.which("setpriv") is None or os.geteuid() != 0,
    reason="nécessite TEST_DATABASE_URL, setpriv et root (pour changer d'utilisateur)")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _snapshot(root: pathlib.Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.fixture(scope="module")
def srv():
    import tempfile
    base = pathlib.Path(tempfile.mkdtemp(prefix="akwaba-ro-", dir="/tmp"))   # traversable par nobody
    base.chmod(0o755)
    srv = base / "srv"
    srv.mkdir()
    shutil.copy(BACKEND / "alembic.ini", srv)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(BACKEND / "alembic", srv / "alembic", ignore=ignore)
    shutil.copytree(BACKEND / "app", srv / "app", ignore=ignore)
    for p in [srv, *srv.rglob("*")]:
        p.chmod(0o555 if p.is_dir() else 0o444)
    tmp = base / "tmp"                      # équivalent du tmpfs /tmp du conteneur
    tmp.mkdir(mode=0o1777)
    tmp.chmod(0o1777)
    yield srv, tmp
    for p in [srv, *srv.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="module")
def env(srv):
    from cryptography.fernet import Fernet
    _, tmp = srv
    return {
        "PATH": os.environ["PATH"], "HOME": "/nonexistent", "TMPDIR": str(tmp), "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"], "SECRET_KEY": "k" * 48,
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(), "ALLOWED_ORIGINS": "https://admin.test",
        "REVOCATION_MODE": "dry_run", "ENFORCER_ENABLED": "false", "LOG_LEVEL": "WARNING",
    }


def _as_nobody(cmd, srv_dir, env, **kw):
    return subprocess.run([*NOBODY, *cmd], cwd=srv_dir, env=env, capture_output=True, text=True, timeout=120, **kw)


def test_migrations_and_check_run_read_only(srv, env):
    srv_dir, _ = srv
    before = _snapshot(srv_dir)
    for args in (["downgrade", "base"], ["upgrade", "head"], ["check"]):
        res = _as_nobody([sys.executable, "-m", "alembic", *args], srv_dir, env)
        assert res.returncode == 0, (args, res.stdout, res.stderr)
    assert _snapshot(srv_dir) == before, "alembic n'écrit rien dans /srv"


def test_http_journey_on_a_read_only_root(srv, env):
    sys.path.insert(0, str(ROOT / "tests" / "e2e"))
    from conftest import dockerfile_uvicorn_flags
    sys.path.insert(0, str(BACKEND))
    from app.core.security import hash_password
    from sqlalchemy import create_engine, text

    srv_dir, tmp = srv
    engine = create_engine(env["DATABASE_URL"])
    with engine.begin() as c:
        c.execute(text("INSERT INTO admin_users (email, password_hash, is_active, display_name) "
                       "VALUES ('ro@example.test', :h, true, 'RO') ON CONFLICT DO NOTHING"),
                  {"h": hash_password("mot-de-passe-solide-ro")})
    engine.dispose()

    before = _snapshot(srv_dir)
    port = _free_port()
    log = tmp / "uvicorn.log"
    proc = subprocess.Popen([*NOBODY, sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port), *dockerfile_uvicorn_flags()],
                            cwd=srv_dir, env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(method, path, body=None, headers=None):
        req = urllib.request.Request(base + path, method=method, headers=headers or {},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    try:
        for _ in range(100):
            try:
                if call("GET", "/api/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        origin = {"Origin": "https://admin.test", "Content-Type": "application/json"}
        status, headers, _ = call("POST", "/api/auth/login",
                                  {"email": "ro@example.test", "password": "mot-de-passe-solide-ro"}, origin)
        assert status == 200
        cookie = headers["Set-Cookie"].split(";")[0]
        status, _, me = call("GET", "/api/auth/me", headers={"Cookie": cookie})
        assert status == 200
        auth = {**origin, "Cookie": cookie, "X-CSRF-Token": json.loads(me)["csrf_token"]}
        status, _, body = call("POST", "/api/members", {"full_name": "Lecture Seule"}, auth)
        assert status == 201, body
        status, headers, csv = call("GET", "/api/reports/operations?format=csv", headers={"Cookie": cookie})
        assert status == 200 and csv.startswith("﻿")
        assert call("POST", "/api/auth/logout", {}, auth)[0] == 204
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    output = log.read_text()
    assert "Read-only file system" not in output and "Permission denied" not in output, output
    assert "Traceback" not in output, output
    assert _snapshot(srv_dir) == before, "aucun fichier créé dans /srv"
