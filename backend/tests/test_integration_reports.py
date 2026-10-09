"""Intégration lot 5b : rapports et audit (PostgreSQL réel, horloge contrôlée)."""
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
    app.dependency_overrides[get_clock] = lambda: FixedClock(datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc))
    with TestClient(app, base_url="https://testserver") as c:
        email = f"{uuid.uuid4().hex[:10]}@example.test"
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO admin_users (email, password_hash) VALUES (:e, :h)"), {"e": email, "h": HASH})
        r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200
        ns = SimpleNamespace(c=c, h={"X-CSRF-Token": r.json()["csrf_token"]})
        ns.plans = {p["code"]: p["id"] for p in c.get("/api/plans").json()}
        ns.methods = {m["code"]: m["id"] for m in c.get("/api/payment-methods").json()}
        ns.cats = {x["name"]: x["id"] for x in c.get("/api/expense-categories").json()}
        yield ns


def uid() -> str:
    return uuid.uuid4().hex[:8]


def paid_sub(api, plan, paid_at, method="CASH", **extra) -> dict:
    member = api.c.post("/api/members", json={"full_name": f"Client {uid()}"}, headers=api.h).json()["id"]
    body = {"member_id": member, "plan_id": api.plans[plan],
            "payment": {"payment_method_id": api.methods[method], "paid_at": paid_at, **extra}}
    r = api.c.post("/api/subscriptions", json=body, headers=api.h)
    assert r.status_code == 201, r.text
    return r.json()


def expense(api, category, amount, date):
    r = api.c.post("/api/expenses", headers=api.h,
                   json={"category_id": api.cats[category], "amount": amount, "expense_date": date})
    assert r.status_code == 201, r.text
    return r.json()


def rev(api, **params):
    r = api.c.get("/api/reports/revenue", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def table(report):
    return [(r["label"], r["count"], r["amount_xof"]) for r in report["rows"]]


def test_reports_and_audit_require_authentication(engine):
    with TestClient(create_app(), base_url="https://testserver") as anon:
        for path in ("/api/reports/revenue", "/api/reports/revenue-by-plan", "/api/reports/expenses",
                     "/api/reports/profit", "/api/audit"):
            assert anon.get(path).status_code == 401


def test_revenue_by_month_quarter_year_and_voided_payments(api):
    paid_sub(api, "M1", "2031-01-15T10:00:00Z")                      # 3273
    paid_sub(api, "M3", "2031-01-20T10:00:00Z")                      # 8521
    c = paid_sub(api, "M1", "2031-03-02T10:00:00Z", settled_amount=3200)
    span = dict(date_from="2031-01-01", date_to="2031-12-31")
    month = rev(api, group_by="month", **span)
    assert table(month) == [("2031-01", 2, 11794), ("2031-03", 1, 3200)]
    assert (month["count"], month["total_xof"]) == (3, 14994)
    assert table(rev(api, group_by="quarter", **span)) == [("2031-Q1", 3, 14994)]
    assert table(rev(api, group_by="year", **span)) == [("2031", 3, 14994)]
    api.c.post(f"/api/payments/{c['payments'][0]['id']}/void", json={"reason": "erreur"}, headers=api.h)
    assert rev(api, group_by="month", **span)["total_xof"] == 11794


def test_year_boundaries_use_the_utc_date(api):
    paid_sub(api, "M1", "2033-12-31T23:59:00Z")
    paid_sub(api, "M1", "2034-01-01T00:00:00Z")
    assert rev(api, group_by="year", date_from="2033-01-01", date_to="2033-12-31")["count"] == 1
    assert rev(api, group_by="year", date_from="2034-01-01", date_to="2034-12-31")["count"] == 1


def test_empty_period_returns_zero(api):
    r = rev(api, date_from="2090-01-01", date_to="2090-12-31")
    assert (r["count"], r["total_xof"], r["rows"]) == (0, 0, [])


def test_revenue_by_plan(api):
    paid_sub(api, "M1", "2035-02-01T10:00:00Z")
    paid_sub(api, "M1", "2035-02-02T10:00:00Z")
    paid_sub(api, "M3", "2035-02-03T10:00:00Z")
    r = api.c.get("/api/reports/revenue-by-plan", params={"date_from": "2035-01-01", "date_to": "2035-12-31"}).json()
    assert r["group_by"] == "plan"
    assert [(x["key"], x["count"], x["amount_xof"]) for x in r["rows"]] == [("M1", 2, 6546), ("M3", 1, 8521)]
    assert r["total_xof"] == 15067


def test_revenue_by_payment_method(api):
    paid_sub(api, "M1", "2036-05-01T10:00:00Z", "CASH")
    paid_sub(api, "M1", "2036-05-02T10:00:00Z", "WAVE")
    paid_sub(api, "M3", "2036-05-03T10:00:00Z", "WAVE")
    r = rev(api, group_by="payment_method", date_from="2036-01-01", date_to="2036-12-31")
    assert [(x["key"], x["count"], x["amount_xof"]) for x in r["rows"]] == [
        ("CASH", 1, 3273), ("WAVE", 2, 11794)]


def test_expenses_by_month_and_category_ignore_voided(api):
    expense(api, "Hébergement", 10000, "2037-01-05")
    expense(api, "Marketing", 5000, "2037-01-20")
    expense(api, "Hébergement", 2000, "2037-02-01")
    gone = expense(api, "Marketing", 999, "2037-02-10")
    api.c.post(f"/api/expenses/{gone['id']}/void", json={"reason": "doublon"}, headers=api.h)
    span = dict(date_from="2037-01-01", date_to="2037-12-31")
    month = api.c.get("/api/reports/expenses", params={"group_by": "month", **span}).json()
    assert table(month) == [("2037-01", 2, 15000), ("2037-02", 1, 2000)] and month["total_xof"] == 17000
    cat = api.c.get("/api/reports/expenses", params={"group_by": "category", **span}).json()
    assert [(x["label"], x["count"], x["amount_xof"]) for x in cat["rows"]] == [
        ("Hébergement", 2, 12000), ("Marketing", 1, 5000)]
    assert table(api.c.get("/api/reports/expenses", params={"group_by": "quarter", **span}).json()) == [
        ("2037-Q1", 3, 17000)]


def test_profit_by_month_with_a_loss_month(api):
    paid_sub(api, "M1", "2038-01-10T10:00:00Z")   # 3273
    paid_sub(api, "M3", "2038-02-10T10:00:00Z")   # 8521
    expense(api, "Frais de paiement", 1000, "2038-02-15")
    expense(api, "Frais de paiement", 500, "2038-03-01")
    r = api.c.get("/api/reports/profit", params={"group_by": "month", "date_from": "2038-01-01",
                                                 "date_to": "2038-12-31"}).json()
    assert [(x["label"], x["revenue_xof"], x["expenses_xof"], x["profit_xof"]) for x in r["rows"]] == [
        ("2038-01", 3273, 0, 3273), ("2038-02", 8521, 1000, 7521), ("2038-03", 0, 500, -500)]
    assert (r["revenue_xof"], r["expenses_xof"], r["profit_xof"]) == (11794, 1500, 10294)


def test_invalid_parameters_are_rejected(api):
    assert api.c.get("/api/reports/revenue", params={"date_from": "2031-02-01", "date_to": "2031-01-01"}).status_code == 422
    assert api.c.get("/api/reports/revenue", params={"group_by": "category"}).status_code == 422
    assert api.c.get("/api/reports/expenses", params={"group_by": "plan"}).status_code == 422
    assert api.c.get("/api/reports/profit", params={"group_by": "payment_method"}).status_code == 422
    assert api.c.get("/api/reports/revenue", params={"date_from": "pas-une-date"}).status_code == 422


def test_audit_listing_filters_and_pagination(api):
    e = expense(api, "Autre", 100, "2039-01-01")
    one = api.c.get("/api/audit", params={"entity_id": e["id"]}).json()
    assert one["total"] == 1
    entry = one["items"][0]
    assert (entry["action"], entry["entity_type"]) == ("expense.recorded", "expense")
    assert entry["actor_email"] and entry["details"]["amount"] == 100
    by_action = api.c.get("/api/audit", params={"action": "expense.recorded"}).json()
    assert by_action["total"] >= 1 and all(i["action"] == "expense.recorded" for i in by_action["items"])
    by_type = api.c.get("/api/audit", params={"entity_type": "expense"}).json()
    assert by_type["total"] >= 1 and all(i["entity_type"] == "expense" for i in by_type["items"])
    page = api.c.get("/api/audit", params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["total"] >= 2
    ids = [i["id"] for i in api.c.get("/api/audit", params={"limit": 5}).json()["items"]]
    assert ids == sorted(ids, reverse=True)
    assert api.c.get("/api/audit", params={"date_from": "2100-01-01"}).json()["total"] == 0
    assert api.c.get("/api/audit", params={"date_from": "2100-02-01", "date_to": "2100-01-01"}).status_code == 422
    assert api.c.get("/api/audit", params={"limit": 1000}).status_code == 422
    assert api.c.get("/api/audit", params={"actor_admin_id": "pas-un-uuid"}).status_code == 422
