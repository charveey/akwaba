"""Intégration Phase 3 : login, cookie, CSRF, verrouillage, expiration, audit (PostgreSQL réel)."""
import os
import pathlib
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.db.session import get_db
from app.main import create_app

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent
PASSWORD = "correct-horse-battery"
HASH = hash_password(PASSWORD)
COOKIE = "akwaba_session"


def alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": URL}
    try:
        subprocess.run(["alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        pytest.fail(f"alembic {' '.join(args)} a échoué:\n{exc.stderr}")


@pytest.fixture(scope="module")
def engine():
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    eng = create_engine(URL)
    yield eng
    eng.dispose()
    alembic("downgrade", "base")


@pytest.fixture
def client(engine):
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = maker()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override
    # https : le cookie est Secure, httpx ne l'enverrait pas en http.
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def make_admin(engine, active=True):
    email = f"{uuid.uuid4().hex[:10]}@example.test"
    with engine.begin() as c:
        admin_id = c.execute(
            text("INSERT INTO admin_users (email, password_hash, is_active) VALUES (:e, :h, :a) RETURNING id"),
            {"e": email, "h": HASH, "a": active},
        ).scalar_one()
    return email, admin_id


def login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_sets_secure_cookie_and_me_works(engine, client):
    email, _ = make_admin(engine)
    r = login(client, email.upper())  # l'e-mail est insensible à la casse
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert r.json()["csrf_token"]
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == email


def test_failures_are_indistinguishable(engine, client):
    email, _ = make_admin(engine)
    wrong = login(client, email, "wrong-password-xx")
    unknown = login(client, "nobody-here@example.test")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_lockout_after_five_failures(engine, client):
    email, admin_id = make_admin(engine)
    for _ in range(5):
        assert login(client, email, "wrong-password-xx").status_code == 401
    assert login(client, email).status_code == 401  # bon mot de passe, mais verrouillé
    with engine.connect() as c:
        locked = c.execute(text("SELECT locked_until IS NOT NULL FROM admin_users WHERE id = :i"), {"i": admin_id}).scalar_one()
    assert locked
    with engine.begin() as c:
        c.execute(text("UPDATE admin_users SET locked_until = now() - interval '1 minute' WHERE id = :i"), {"i": admin_id})
    assert login(client, email).status_code == 200
    with engine.connect() as c:
        count = c.execute(text("SELECT failed_login_count FROM admin_users WHERE id = :i"), {"i": admin_id}).scalar_one()
    assert count == 0


def test_logout_requires_csrf_and_revokes_session(engine, client):
    email, _ = make_admin(engine)
    csrf = login(client, email).json()["csrf_token"]
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "faux"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_me_requires_authentication(client):
    assert client.get("/api/auth/me").status_code == 401


def test_idle_session_expires(engine, client):
    email, admin_id = make_admin(engine)
    login(client, email)
    with engine.begin() as c:
        c.execute(text("UPDATE admin_sessions SET last_seen_at = now() - interval '13 hours' WHERE admin_user_id = :i"), {"i": admin_id})
    assert client.get("/api/auth/me").status_code == 401


def test_absolute_session_expires(engine, client):
    email, admin_id = make_admin(engine)
    login(client, email)
    with engine.begin() as c:
        c.execute(text("UPDATE admin_sessions SET expires_at = now() - interval '1 second' WHERE admin_user_id = :i"), {"i": admin_id})
    assert client.get("/api/auth/me").status_code == 401


def test_inactive_admin_cannot_login_nor_keep_session(engine, client):
    email, admin_id = make_admin(engine)
    assert login(client, email).status_code == 200
    with engine.begin() as c:
        c.execute(text("UPDATE admin_users SET is_active = false WHERE id = :i"), {"i": admin_id})
    assert client.get("/api/auth/me").status_code == 401
    assert login(client, email).status_code == 401


def test_audit_trail_is_written(engine, client):
    email, admin_id = make_admin(engine)
    login(client, email, "wrong-password-xx")
    login(client, email)
    with engine.connect() as c:
        actions = {r[0] for r in c.execute(text("SELECT action FROM audit_logs WHERE actor_admin_id = :i"), {"i": admin_id})}
    assert {"admin.login_failed", "admin.login_success"} <= actions


def test_session_token_is_not_stored_in_clear(engine, client):
    email, admin_id = make_admin(engine)
    login(client, email)
    raw = client.cookies.get(COOKIE)
    assert raw
    with engine.connect() as c:
        stored = [r[0] for r in c.execute(text("SELECT token_hash FROM admin_sessions WHERE admin_user_id = :i"), {"i": admin_id})]
    assert stored and raw not in stored and all(len(s) == 64 for s in stored)
