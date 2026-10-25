"""Intégration lot 9a : identités protégées (PostgreSQL réel, horloge contrôlée)."""
import os
import pathlib
import subprocess
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_clock
from app.core.security import hash_password
from app.db.session import get_db
from app.domain.clock import FixedClock
from app.main import create_app
from app.services import protected as svc

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent
PASSWORD = "correct-horse-battery"
HASH = hash_password(PASSWORD)
NOW = datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc)
BASE = "/api/protected-identities"


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
def api(engine):
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = maker()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_clock] = lambda: FixedClock(NOW)
    with TestClient(app, base_url="https://testserver") as c:
        email = f"{uuid.uuid4().hex[:10]}@example.test"
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO admin_users (email, password_hash) VALUES (:e, :h)"), {"e": email, "h": HASH})
        r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200
        yield SimpleNamespace(c=c, h={"X-CSRF-Token": r.json()["csrf_token"]}, maker=maker, engine=engine)


def login() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.test"


def audit(engine, entity_id):
    with engine.connect() as c:
        return c.execute(text("SELECT action, details, actor_label FROM audit_logs WHERE entity_id = :i ORDER BY id"),
                         {"i": str(entity_id)}).all()


def test_endpoints_require_authentication_and_csrf(engine, api):
    with TestClient(create_app(), base_url="https://testserver") as anon:
        assert anon.get(BASE).status_code == 401
    assert api.c.post(BASE, json={"login_name": login()}).status_code == 403
    assert api.c.post(f"{BASE}/{uuid.uuid4()}/remove").status_code == 403


def test_add_list_and_remove(api):
    name = login().upper()
    r = api.c.post(BASE, json={"login_name": f"  {name}  ", "reason": "  mon appareil  "}, headers=api.h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["login_name"], body["source"], body["reason"], body["active"]) == (
        name.lower(), "MANUAL", "mon appareil", True)
    assert body["id"] in [x["id"] for x in api.c.get(BASE).json()]
    gone = api.c.post(f"{BASE}/{body['id']}/remove", headers=api.h)
    assert gone.status_code == 200 and gone.json()["active"] is False and gone.json()["removed_at"].startswith("2040-01-01")
    assert body["id"] not in [x["id"] for x in api.c.get(BASE).json()]
    assert body["id"] in [x["id"] for x in api.c.get(BASE, params={"include_removed": True}).json()]


def test_a_login_cannot_be_protected_twice_but_can_be_again_after_removal(api):
    name = login()
    first = api.c.post(BASE, json={"login_name": name}, headers=api.h).json()
    assert api.c.post(BASE, json={"login_name": name.upper()}, headers=api.h).status_code == 409
    assert api.c.post(f"{BASE}/{first['id']}/remove", headers=api.h).status_code == 200
    again = api.c.post(BASE, json={"login_name": name}, headers=api.h)
    assert again.status_code == 201 and again.json()["id"] != first["id"]


def test_removal_errors(api):
    assert api.c.post(f"{BASE}/{uuid.uuid4()}/remove", headers=api.h).status_code == 404
    p = api.c.post(BASE, json={"login_name": login()}, headers=api.h).json()
    assert api.c.post(f"{BASE}/{p['id']}/remove", headers=api.h).status_code == 200
    assert api.c.post(f"{BASE}/{p['id']}/remove", headers=api.h).status_code == 409


def test_automatic_protections_cannot_be_removed_by_hand(api):
    with api.engine.begin() as c:
        pid = c.execute(text("INSERT INTO protected_identities (login_name, source) VALUES (:l, 'AUTO_DEVICE') "
                             "RETURNING id"), {"l": login()}).scalar_one()
    assert api.c.post(f"{BASE}/{pid}/remove", headers=api.h).status_code == 409
    assert any(x["id"] == str(pid) and x["source"] == "AUTO_DEVICE" for x in api.c.get(BASE).json())


def test_validation(api):
    for body in ({}, {"login_name": ""}, {"login_name": "a b@x.test"}, {"login_name": "x" * 321},
                 {"login_name": 5}, {"login_name": login(), "reason": 5}, {"login_name": login(), "bonus": 1},
                 {"login_name": login(), "reason": "x" * 501}):
        assert api.c.post(BASE, json=body, headers=api.h).status_code == 422, body
    assert api.c.post(BASE, json={"login_name": login(), "reason": "   "}, headers=api.h).json()["reason"] is None


def test_audit_holds_the_login_but_never_the_free_text_reason(api):
    name = login()
    p = api.c.post(BASE, json={"login_name": name, "reason": "motif-secret-xyz"}, headers=api.h).json()
    api.c.post(f"{BASE}/{p['id']}/remove", headers=api.h)
    rows = audit(api.engine, p["id"])
    assert [a for a, _, _ in rows] == ["protected_identity.added", "protected_identity.removed"]
    assert rows[0][1] == {"login_name": name, "source": "MANUAL"}
    assert "motif-secret-xyz" not in str(rows)


def test_the_cli_path_works_without_an_admin_and_is_audited_as_cli(api):
    name = login()
    with api.maker() as db:
        out = svc.add_protected(db, login=name, reason=None, admin_id=None, label="cli")
    assert out.source == "MANUAL" and out.active
    rows = audit(api.engine, out.id)
    assert rows == [("protected_identity.added", {"login_name": name, "source": "MANUAL"}, "cli")]
    with api.maker() as db, pytest.raises(Exception):
        svc.add_protected(db, login=name, reason=None, admin_id=None, label="cli")
