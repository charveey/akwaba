"""Intégration lot 5a : catégories et dépenses (PostgreSQL réel, horloge contrôlée)."""
import os
import pathlib
import subprocess
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
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
    app.dependency_overrides[get_clock] = lambda: FixedClock(datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc))
    with TestClient(app, base_url="https://testserver") as c:
        email = f"{uuid.uuid4().hex[:10]}@example.test"
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO admin_users (email, password_hash) VALUES (:e, :h)"), {"e": email, "h": HASH})
        r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200
        ns = SimpleNamespace(c=c, h={"X-CSRF-Token": r.json()["csrf_token"]})
        ns.cats = {x["name"]: x["id"] for x in c.get("/api/expense-categories").json()}
        yield ns


def uid() -> str:
    return uuid.uuid4().hex[:8]


def new_category(api, name=None):
    r = api.c.post("/api/expense-categories", json={"name": name or f"Cat {uid()}"}, headers=api.h)
    assert r.status_code == 201, r.text
    return r.json()


def new_expense(api, category_id, amount=1000, date="2026-03-10", **extra):
    return api.c.post("/api/expenses", headers=api.h,
                      json={"category_id": category_id, "amount": amount, "expense_date": date, **extra})


def audit(engine, entity_id):
    with engine.connect() as c:
        return c.execute(text("SELECT action, details FROM audit_logs WHERE entity_id = :i ORDER BY id"),
                         {"i": str(entity_id)}).all()


def test_endpoints_require_authentication_and_csrf(engine, api):
    with TestClient(create_app(), base_url="https://testserver") as anon:
        assert anon.get("/api/expense-categories").status_code == 401
        assert anon.get("/api/expenses").status_code == 401
    assert api.c.post("/api/expenses", json={}).status_code == 403
    assert api.c.post("/api/expense-categories", json={"name": "X"}).status_code == 403


def test_initial_categories_are_seeded(api):
    assert set(api.cats) >= {"Hébergement", "Domaines et logiciels", "Frais de paiement", "Marketing", "Autre"}


def test_category_create_rename_deactivate(api, engine):
    cat = new_category(api)
    assert api.c.post(
        "/api/expense-categories", json={"name": cat["name"].upper()}, headers=api.h).status_code == 409
    new_name = f"Renommée {uid()}"
    r = api.c.put(f"/api/expense-categories/{cat['id']}", json={"name": new_name, "is_active": True}, headers=api.h)
    assert r.status_code == 200 and r.json()["name"] == new_name
    other = new_category(api)
    clash = api.c.put(f"/api/expense-categories/{other['id']}", json={"name": new_name.lower(), "is_active": True},
                      headers=api.h)
    assert clash.status_code == 409
    off = api.c.put(f"/api/expense-categories/{cat['id']}", json={"name": new_name, "is_active": False}, headers=api.h)
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert cat["id"] not in [c["id"] for c in api.c.get("/api/expense-categories").json()]
    assert cat["id"] in [c["id"] for c in api.c.get("/api/expense-categories", params={"include_inactive": True}).json()]
    assert api.c.put(f"/api/expense-categories/{uuid.uuid4()}", json={"name": "Z", "is_active": True},
                     headers=api.h).status_code == 404
    assert [a for a, _ in audit(engine, cat["id"])] == [
        "expense_category.created", "expense_category.updated", "expense_category.updated"]


def test_create_expense_and_audit_has_no_free_text(api, engine):
    r = new_expense(api, api.cats["Hébergement"], 15000, description="  serveur secret-xyz ")
    assert r.status_code == 201, r.text
    e = r.json()
    assert (e["amount"], e["currency"], e["status"], e["category_name"]) == (15000, "XOF", "ACTIVE", "Hébergement")
    assert e["description"] == "serveur secret-xyz"
    rows = audit(engine, e["id"])
    assert [a for a, _ in rows] == ["expense.recorded"]
    assert "secret-xyz" not in str(rows[0][1]) and rows[0][1]["amount"] == 15000


def test_expense_validation(api):
    cid = api.cats["Marketing"]
    assert new_expense(api, cid, date="2026-03-11").status_code == 422     # futur
    assert new_expense(api, cid, date="2026-03-10").status_code == 201     # aujourd'hui
    assert new_expense(api, cid, date="2025-01-01").status_code == 201     # antidatage
    assert new_expense(api, cid, amount=0).status_code == 422
    assert new_expense(api, cid, amount=10.5).status_code == 422
    assert new_expense(api, cid, currency="EUR").status_code == 422
    assert new_expense(api, str(uuid.uuid4())).status_code == 404


def test_inactive_category_refuses_new_expenses_but_keeps_old_ones(api):
    cat = new_category(api)
    old = new_expense(api, cat["id"]).json()
    api.c.put(f"/api/expense-categories/{cat['id']}", json={"name": cat["name"], "is_active": False}, headers=api.h)
    assert new_expense(api, cat["id"]).status_code == 409
    assert api.c.get(f"/api/expenses/{old['id']}").json()["category_name"] == cat["name"]


def test_void_expense(api, engine):
    e = new_expense(api, api.cats["Autre"]).json()
    assert api.c.post(f"/api/expenses/{e['id']}/void", json={"reason": " "}, headers=api.h).status_code == 422
    r = api.c.post(f"/api/expenses/{e['id']}/void", json={"reason": "doublon de saisie"}, headers=api.h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "VOIDED" and body["void_reason"] == "doublon de saisie" and body["voided_at"].startswith("2026-03-10")
    assert api.c.post(f"/api/expenses/{e['id']}/void", json={"reason": "x"}, headers=api.h).status_code == 409
    assert api.c.post(f"/api/expenses/{uuid.uuid4()}/void", json={"reason": "x"}, headers=api.h).status_code == 404
    rows = audit(engine, e["id"])
    assert [a for a, _ in rows] == ["expense.recorded", "expense.voided"]
    assert "doublon" not in str(rows[1][1])


def test_list_filters_and_total_amount_ignore_voided(api):
    cat = new_category(api)
    a = new_expense(api, cat["id"], 1000, "2026-02-01").json()
    new_expense(api, cat["id"], 2500, "2026-03-05")
    c = new_expense(api, cat["id"], 4000, "2026-03-09").json()
    api.c.post(f"/api/expenses/{c['id']}/void", json={"reason": "erreur"}, headers=api.h)
    page = api.c.get("/api/expenses", params={"category_id": cat["id"]}).json()
    assert page["total"] == 3 and page["total_amount"] == 3500
    assert [x["expense_date"] for x in page["items"]] == ["2026-03-09", "2026-03-05", "2026-02-01"]
    march = api.c.get("/api/expenses", params={"category_id": cat["id"], "date_from": "2026-03-01",
                                               "date_to": "2026-03-31"}).json()
    assert march["total"] == 2 and march["total_amount"] == 2500
    voided = api.c.get("/api/expenses", params={"category_id": cat["id"], "status": "VOIDED"}).json()
    assert voided["total"] == 1 and voided["total_amount"] == 0
    assert api.c.get("/api/expenses", params={"date_from": "2026-04-01", "date_to": "2026-03-01"}).status_code == 422
    assert api.c.get("/api/expenses", params={"status": "bogus"}).status_code == 422
    assert api.c.get("/api/expenses", params={"limit": 1000}).status_code == 422
    assert api.c.get(f"/api/expenses/{a['id']}").status_code == 200
    assert api.c.get(f"/api/expenses/{uuid.uuid4()}").status_code == 404


def _raw_expense(c, category_id, amount=1000, currency="XOF", status="ACTIVE"):
    voided = status == "VOIDED"
    return c.execute(
        text("INSERT INTO expenses (category_id, amount, currency, expense_date, status, voided_at, void_reason) "
             "VALUES (:c, :a, :cur, '2026-03-10', :s, "
             + ("now(), 'x'" if voided else "NULL, NULL")
             + ") RETURNING id"),
        {"c": category_id, "a": amount, "cur": currency, "s": status}).scalar_one()


def test_database_guards(api, engine):
    cid = api.cats["Autre"]
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            _raw_expense(c, cid, currency="EUR")
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            _raw_expense(c, cid, amount=0)
    with engine.begin() as c:
        eid = _raw_expense(c, cid)
    with pytest.raises(DBAPIError, match="immuables"):
        with engine.begin() as c:
            c.execute(text("UPDATE expenses SET amount = 1 WHERE id = :i"), {"i": eid})
    with pytest.raises(DBAPIError, match="interdit"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM expenses WHERE id = :i"), {"i": eid})
    with engine.begin() as c:
        voided = _raw_expense(c, cid, status="VOIDED")
    with pytest.raises(DBAPIError, match="irreversible"):
        with engine.begin() as c:
            c.execute(text("UPDATE expenses SET status = 'ACTIVE', voided_at = NULL, void_reason = NULL WHERE id = :i"),
                      {"i": voided})
    with pytest.raises(IntegrityError):  # une catégorie utilisée ne se supprime pas
        with engine.begin() as c:
            c.execute(text("DELETE FROM expense_categories WHERE id = :i"), {"i": cid})
