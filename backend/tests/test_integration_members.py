"""Intégration lot 4b : API membres et identités, CSRF, audit, archivage (PostgreSQL réel)."""
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

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent
PASSWORD = "correct-horse-battery"
HASH = hash_password(PASSWORD)


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
    with TestClient(app, base_url="https://testserver") as c:
        email = f"{uuid.uuid4().hex[:10]}@example.test"
        with engine.begin() as conn:
            admin_id = conn.execute(
                text("INSERT INTO admin_users (email, password_hash) VALUES (:e, :h) RETURNING id"),
                {"e": email, "h": HASH},
            ).scalar_one()
        r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200
        yield SimpleNamespace(c=c, h={"X-CSRF-Token": r.json()["csrf_token"]}, admin_id=str(admin_id), app=app)


def uid() -> str:
    return uuid.uuid4().hex[:10]


def new_member(api, **over):
    r = api.c.post("/api/members", json={"full_name": f"Client {uid()}", **over}, headers=api.h)
    assert r.status_code == 201, r.text
    return r.json()


def link(api, member_id, login):
    return api.c.post(f"/api/members/{member_id}/identities", json={"login_name": login}, headers=api.h)


def audit_rows(engine, entity_id):
    with engine.connect() as c:
        return c.execute(
            text("SELECT action, actor_admin_id, entity_type, details FROM audit_logs WHERE entity_id = :i ORDER BY id"),
            {"i": str(entity_id)},
        ).all()


def test_endpoints_require_authentication(engine):
    app = create_app()
    with TestClient(app, base_url="https://testserver") as c:
        assert c.get("/api/members").status_code == 401
        assert c.post("/api/members", json={"full_name": "X"}).status_code == 401


def test_writes_require_csrf(api):
    r = api.c.post("/api/members", json={"full_name": "X"})
    assert r.status_code == 403
    r = api.c.post("/api/members", json={"full_name": "X"}, headers={"X-CSRF-Token": "faux"})
    assert r.status_code == 403


def test_create_normalizes_and_audits_without_personal_data(api, engine):
    m = new_member(api, email="Awa@Example.TEST", phone_e164="+225 01 02 03 04 05", country_code="ci", notes="secret")
    assert m["email"] == "awa@example.test"
    assert m["phone_e164"] == "+2250102030405"
    assert m["country_code"] == "CI"
    rows = audit_rows(engine, m["id"])
    assert [r[0] for r in rows] == ["member.created"]
    assert str(rows[0][1]) == api.admin_id and rows[0][2] == "member"
    assert "awa@example.test" not in str(rows[0][3]) and "secret" not in str(rows[0])


def test_invalid_payloads_are_rejected(api):
    assert api.c.post("/api/members", json={"full_name": "X", "phone_e164": "0102"}, headers=api.h).status_code == 422
    assert api.c.post("/api/members", json={"full_name": "X", "bogus": 1}, headers=api.h).status_code == 422
    assert api.c.post("/api/members", json={"full_name": "  "}, headers=api.h).status_code == 422


def test_read_unknown_member_and_bad_uuid(api):
    assert api.c.get(f"/api/members/{uuid.uuid4()}").status_code == 404
    assert api.c.get("/api/members/pas-un-uuid").status_code == 422


def test_update_audits_changed_field_names_only(api, engine):
    m = new_member(api, email="a@example.test")
    r = api.c.put(f"/api/members/{m['id']}", json={"full_name": "Nouveau Nom", "email": "a@example.test"}, headers=api.h)
    assert r.status_code == 200 and r.json()["full_name"] == "Nouveau Nom"
    rows = audit_rows(engine, m["id"])
    assert rows[-1][0] == "member.updated"
    assert set(rows[-1][3]["changed_fields"]) == {"full_name"}
    assert "Nouveau Nom" not in str(rows[-1][3])


def test_noop_update_writes_no_audit(api, engine):
    m = new_member(api, email="same@example.test")
    r = api.c.put(f"/api/members/{m['id']}", json={"full_name": m["full_name"], "email": "same@example.test"}, headers=api.h)
    assert r.status_code == 200
    assert [x[0] for x in audit_rows(engine, m["id"])] == ["member.created"]


def test_link_identity_and_uniqueness(api, engine):
    login = f"{uid()}@example.test"
    a, b = new_member(api), new_member(api)
    r = link(api, a["id"], login.upper())
    assert r.status_code == 201 and r.json()["login_name"] == login and r.json()["is_active"] is True
    assert link(api, b["id"], login).status_code == 409
    assert link(api, a["id"], login).status_code == 409
    rows = audit_rows(engine, r.json()["id"])
    assert rows[0][0] == "identity.linked" and rows[0][3]["member_id"] == a["id"]
    detail = api.c.get(f"/api/members/{a['id']}").json()
    assert [i["login_name"] for i in detail["identities"]] == [login]


def test_unlink_then_relink_on_another_member(api):
    login = f"{uid()}@example.test"
    a, b = new_member(api), new_member(api)
    ident = link(api, a["id"], login).json()
    r = api.c.post(f"/api/members/{a['id']}/identities/{ident['id']}/unlink", headers=api.h)
    assert r.status_code == 200 and r.json()["is_active"] is False and r.json()["unlinked_at"]
    assert api.c.post(f"/api/members/{a['id']}/identities/{ident['id']}/unlink", headers=api.h).status_code == 409
    assert link(api, b["id"], login).status_code == 201


def test_unlink_with_wrong_member_is_404(api):
    a, b = new_member(api), new_member(api)
    ident = link(api, a["id"], f"{uid()}@example.test").json()
    assert api.c.post(f"/api/members/{b['id']}/identities/{ident['id']}/unlink", headers=api.h).status_code == 404


def test_archive_unlinks_identities_and_blocks_changes(api, engine):
    m = new_member(api)
    login = f"{uid()}@example.test"
    link(api, m["id"], login)
    r = api.c.post(f"/api/members/{m['id']}/archive", headers=api.h)
    assert r.status_code == 200
    body = r.json()
    assert body["archived_at"]
    assert all(i["is_active"] is False and i["unlinked_at"] for i in body["identities"])
    assert audit_rows(engine, m["id"])[-1][3]["unlinked_logins"] == [login]
    assert api.c.post(f"/api/members/{m['id']}/archive", headers=api.h).status_code == 409
    assert link(api, m["id"], f"{uid()}@example.test").status_code == 409
    assert api.c.put(f"/api/members/{m['id']}", json={"full_name": "X"}, headers=api.h).status_code == 409
    other = new_member(api)
    assert link(api, other["id"], login).status_code == 201  # le login est de nouveau libre


def test_archive_uses_the_injected_clock(api):
    api.app.dependency_overrides[get_clock] = lambda: FixedClock(datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc))
    m = new_member(api)
    link(api, m["id"], f"{uid()}@example.test")
    body = api.c.post(f"/api/members/{m['id']}/archive", headers=api.h).json()
    assert body["archived_at"].startswith("2026-03-01T12:00:00")
    assert body["identities"][0]["unlinked_at"].startswith("2026-03-01T12:00:00")


def test_list_search_pagination_and_archived_filter(api):
    token = uid()
    ids = [new_member(api, full_name=f"Zed {token} {n}")["id"] for n in range(3)]
    page = api.c.get("/api/members", params={"q": token, "limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["limit"] == 2
    assert len(api.c.get("/api/members", params={"q": token, "limit": 2, "offset": 2}).json()["items"]) == 1
    api.c.post(f"/api/members/{ids[0]}/archive", headers=api.h)
    assert api.c.get("/api/members", params={"q": token}).json()["total"] == 2
    assert api.c.get("/api/members", params={"q": token, "include_archived": True}).json()["total"] == 3


def test_search_by_login_and_like_wildcards_are_escaped(api):
    m = new_member(api)
    token = uid()
    link(api, m["id"], f"{token}@example.test")
    found = api.c.get("/api/members", params={"q": token}).json()
    assert found["total"] == 1 and found["items"][0]["id"] == m["id"]
    assert api.c.get("/api/members", params={"q": "%"}).json()["total"] == 0
    assert api.c.get("/api/members", params={"q": "_"}).json()["total"] == 0


def test_limit_is_bounded(api):
    assert api.c.get("/api/members", params={"limit": 0}).status_code == 422
    assert api.c.get("/api/members", params={"limit": 1000}).status_code == 422
