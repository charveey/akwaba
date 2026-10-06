"""Intégration lot 4c : abonnements, renouvellement, paiements (PostgreSQL réel, horloge contrôlée)."""
import os
import pathlib
import subprocess
import uuid
from datetime import datetime, timezone
from decimal import Decimal
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


def set_clock(api, y, m, d, hh=12):
    api.app.dependency_overrides[get_clock] = lambda: FixedClock(datetime(y, m, d, hh, 0, tzinfo=timezone.utc))


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
            conn.execute(text("INSERT INTO admin_users (email, password_hash) VALUES (:e, :h)"), {"e": email, "h": HASH})
        r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200
        ns = SimpleNamespace(c=c, h={"X-CSRF-Token": r.json()["csrf_token"]}, app=app)
        set_clock(ns, 2026, 1, 17)
        ns.plans = {p["code"]: p["id"] for p in c.get("/api/plans").json()}
        ns.methods = {m["code"]: m["id"] for m in c.get("/api/payment-methods").json()}
        yield ns


def uid() -> str:
    return uuid.uuid4().hex[:10]


def new_member(api) -> str:
    r = api.c.post("/api/members", json={"full_name": f"Client {uid()}"}, headers=api.h)
    assert r.status_code == 201
    return r.json()["id"]


def pay(api, method="CASH", **extra) -> dict:
    return {"payment_method_id": api.methods[method], **extra}


def create_sub(api, member_id, plan="M1", with_payment=True, **extra):
    body = {"member_id": member_id, "plan_id": api.plans[plan], **extra}
    if with_payment:
        body["payment"] = pay(api)
    return api.c.post("/api/subscriptions", json=body, headers=api.h)


def renew(api, sub_id, **extra):
    return api.c.post(f"/api/subscriptions/{sub_id}/renew", json={"payment": pay(api), **extra}, headers=api.h)


def audit_actions(engine, entity_id):
    with engine.connect() as c:
        return [r[0] for r in c.execute(
            text("SELECT action FROM audit_logs WHERE entity_id = :i ORDER BY id"), {"i": str(entity_id)})]


def test_endpoints_require_authentication_and_csrf(engine, api):
    anon = create_app()
    with TestClient(anon, base_url="https://testserver") as c:
        for path in ("/api/plans", "/api/payment-methods", "/api/subscriptions", "/api/payments"):
            assert c.get(path).status_code == 401
    assert api.c.post("/api/subscriptions", json={}).status_code == 403
    assert api.c.post("/api/payments", json={}).status_code == 403


def test_reference_data(api):
    plans = {p["code"]: p for p in api.c.get("/api/plans").json()}
    assert plans["M1"]["price_amount"] == 499 and plans["M1"]["price_xof"] == 3273
    assert plans["M12"]["price_xof"] == 35415
    assert plans["PLATINUM"]["is_unlimited"] is True and plans["PLATINUM"]["price_xof"] == 0
    assert {"WAVE", "CASH", "ORANGE_MONEY"} <= set(api.methods)


def test_create_with_payment_computes_dates_statuses_and_settlement(api, engine):
    member = new_member(api)
    r = create_sub(api, member)
    assert r.status_code == 201, r.text
    s = r.json()
    assert (s["starts_on"], s["expires_on"], s["display_end"]) == ("2026-01-17", "2026-02-17", "2026-02-16")
    assert s["grace_end"] == "2026-02-20"
    assert (s["payment_status"], s["access_status"], s["renewal_status"]) == ("PAID", "ACTIVE", "UP_TO_DATE")
    p = s["payments"][0]
    assert (p["amount"], p["currency"], p["settled_amount"], p["settled_currency"]) == (499, "EUR", 3273, "XOF")
    assert Decimal(p["fx_rate"]) == Decimal("655.957") and p["status"] == "CONFIRMED"
    assert audit_actions(engine, s["id"]) == ["subscription.created"]
    assert audit_actions(engine, p["id"]) == ["payment.recorded"]


def test_amount_cannot_be_injected_by_the_client(api):
    member = new_member(api)
    body = {"member_id": member, "plan_id": api.plans["M1"], "payment": pay(api, amount=1)}
    assert api.c.post("/api/subscriptions", json=body, headers=api.h).status_code == 422


def test_custom_settled_amount_is_kept(api):
    s = create_sub(api, new_member(api), payment=None, with_payment=False).json()
    r = api.c.post("/api/payments", json={"subscription_id": s["id"], **pay(api, "WAVE", settled_amount=3200)},
                   headers=api.h)
    assert r.status_code == 201 and r.json()["settled_amount"] == 3200 and r.json()["payment_method_code"] == "WAVE"


def test_pending_subscription_then_payment(api):
    s = create_sub(api, new_member(api), with_payment=False).json()
    assert (s["payment_status"], s["access_status"], s["payments"]) == ("PENDING", "NONE", [])
    r = api.c.post("/api/payments", json={"subscription_id": s["id"], **pay(api)}, headers=api.h)
    assert r.status_code == 201
    again = api.c.post("/api/payments", json={"subscription_id": s["id"], **pay(api)}, headers=api.h)
    assert again.status_code == 409
    got = api.c.get(f"/api/subscriptions/{s['id']}").json()
    assert (got["payment_status"], got["access_status"]) == ("PAID", "ACTIVE")


def test_platinum_is_waived_and_refuses_payment_and_renewal(api):
    s = create_sub(api, new_member(api), "PLATINUM", with_payment=False).json()
    assert (s["payment_status"], s["expires_on"], s["access_status"], s["renewal_status"]) == (
        "WAIVED", None, "ACTIVE", "NOT_APPLICABLE")
    assert api.c.post("/api/payments", json={"subscription_id": s["id"], **pay(api)}, headers=api.h).status_code == 409
    assert renew(api, s["id"]).status_code == 409
    assert create_sub(api, new_member(api), "PLATINUM", with_payment=True).status_code == 422


def test_only_one_open_subscription_per_member(api):
    member = new_member(api)
    first = create_sub(api, member).json()
    assert create_sub(api, member).status_code == 409
    assert api.c.post(f"/api/subscriptions/{first['id']}/cancel", headers=api.h).status_code == 200
    assert create_sub(api, member).status_code == 201


def test_renewal_in_grace_starts_at_previous_expiry(api, engine):
    member = new_member(api)
    first = create_sub(api, member).json()
    set_clock(api, 2026, 2, 19)
    r = renew(api, first["id"])
    assert r.status_code == 201, r.text
    s = r.json()
    assert (s["starts_on"], s["expires_on"]) == ("2026-02-17", "2026-03-17")
    assert s["previous_subscription_id"] == first["id"] and s["access_status"] == "ACTIVE"
    assert api.c.get(f"/api/subscriptions/{first['id']}").json()["renewal_status"] == "UP_TO_DATE"
    assert audit_actions(engine, s["id"]) == ["subscription.renewed"]


def test_renewal_after_grace_starts_at_payment_date(api):
    first = create_sub(api, new_member(api)).json()
    set_clock(api, 2026, 2, 25)
    s = renew(api, first["id"]).json()
    assert (s["starts_on"], s["expires_on"]) == ("2026-02-25", "2026-03-25")


def test_renewal_day_after_grace_and_last_grace_day(api):
    a = create_sub(api, new_member(api)).json()
    set_clock(api, 2026, 2, 19)
    assert renew(api, a["id"]).json()["starts_on"] == "2026-02-17"
    set_clock(api, 2026, 1, 17)
    b = create_sub(api, new_member(api)).json()
    set_clock(api, 2026, 2, 20)
    assert renew(api, b["id"]).json()["starts_on"] == "2026-02-20"


def test_early_renewal_keeps_continuity(api):
    first = create_sub(api, new_member(api)).json()
    set_clock(api, 2026, 2, 10)
    assert renew(api, first["id"]).json()["starts_on"] == "2026-02-17"


def test_month_end_drift_across_renewals(api):
    set_clock(api, 2026, 1, 31)
    first = create_sub(api, new_member(api)).json()
    assert first["expires_on"] == "2026-02-28"
    set_clock(api, 2026, 2, 28)
    second = renew(api, first["id"]).json()
    assert (second["starts_on"], second["expires_on"]) == ("2026-02-28", "2026-03-28")


def test_a_subscription_is_renewed_only_once_and_changing_plan(api):
    first = create_sub(api, new_member(api)).json()
    set_clock(api, 2026, 2, 10)
    r = renew(api, first["id"], plan_id=api.plans["M3"])
    assert r.status_code == 201 and r.json()["plan_code"] == "M3" and r.json()["expires_on"] == "2026-05-17"
    assert renew(api, first["id"]).status_code == 409
    assert api.c.post(f"/api/subscriptions/{first['id']}/cancel", headers=api.h).status_code == 409
    assert renew(api, r.json()["id"], plan_id=api.plans["PLATINUM"]).status_code == 422


def test_cancel_blocks_renewal_and_sets_statuses(api, engine):
    s = create_sub(api, new_member(api)).json()
    c = api.c.post(f"/api/subscriptions/{s['id']}/cancel", headers=api.h)
    assert c.status_code == 200
    body = c.json()
    assert (body["access_status"], body["renewal_status"]) == ("EXPIRED", "NON_RENEWAL") and body["canceled_at"]
    assert api.c.post(f"/api/subscriptions/{s['id']}/cancel", headers=api.h).status_code == 409
    assert renew(api, s["id"]).status_code == 409
    assert audit_actions(engine, s["id"]) == ["subscription.created", "subscription.canceled"]


def test_renew_requires_a_paid_subscription(api):
    s = create_sub(api, new_member(api), with_payment=False).json()
    assert renew(api, s["id"]).status_code == 409


def test_void_payment_reverts_subscription_and_allows_repayment(api, engine):
    s = create_sub(api, new_member(api)).json()
    pid = s["payments"][0]["id"]
    assert api.c.post(f"/api/payments/{pid}/void", json={"reason": "  "}, headers=api.h).status_code == 422
    r = api.c.post(f"/api/payments/{pid}/void", json={"reason": "erreur de saisie"}, headers=api.h)
    assert r.status_code == 200 and r.json()["status"] == "VOIDED" and r.json()["void_reason"] == "erreur de saisie"
    assert api.c.post(f"/api/payments/{pid}/void", json={"reason": "x"}, headers=api.h).status_code == 409
    got = api.c.get(f"/api/subscriptions/{s['id']}").json()
    assert (got["payment_status"], got["access_status"]) == ("PENDING", "NONE")
    assert api.c.post("/api/payments", json={"subscription_id": s["id"], **pay(api)}, headers=api.h).status_code == 201
    assert api.c.get(f"/api/subscriptions/{s['id']}").json()["payment_status"] == "PAID"
    assert audit_actions(engine, pid) == ["payment.recorded", "payment.voided"]
    with engine.connect() as c:
        details = c.execute(text("SELECT details FROM audit_logs WHERE action = 'payment.voided' AND entity_id = :i"),
                            {"i": pid}).scalar_one()
    assert "erreur de saisie" not in str(details)


def test_future_payment_date_is_rejected(api):
    body = {"member_id": new_member(api), "plan_id": api.plans["M1"],
            "payment": pay(api, paid_at="2026-01-18T12:00:00Z")}
    assert api.c.post("/api/subscriptions", json=body, headers=api.h).status_code == 422
    body["payment"]["paid_at"] = "2026-01-18T12:00:00"  # sans fuseau
    assert api.c.post("/api/subscriptions", json=body, headers=api.h).status_code == 422


def test_backdated_payment_defines_the_start_date(api):
    body = {"member_id": new_member(api), "plan_id": api.plans["M1"],
            "payment": pay(api, paid_at="2026-01-10T09:00:00Z")}
    s = api.c.post("/api/subscriptions", json=body, headers=api.h).json()
    assert (s["starts_on"], s["expires_on"]) == ("2026-01-10", "2026-02-10")


def test_archived_member_and_unknown_references(api):
    member = new_member(api)
    api.c.post(f"/api/members/{member}/archive", headers=api.h)
    assert create_sub(api, member).status_code == 409
    other = new_member(api)
    assert api.c.post("/api/subscriptions", json={"member_id": other, "plan_id": str(uuid.uuid4())},
                      headers=api.h).status_code == 404
    bad = {"member_id": other, "plan_id": api.plans["M1"], "payment": {"payment_method_id": str(uuid.uuid4())}}
    assert api.c.post("/api/subscriptions", json=bad, headers=api.h).status_code == 422
    assert api.c.get(f"/api/subscriptions/{uuid.uuid4()}").status_code == 404


def test_lists_and_filters(api):
    member = new_member(api)
    s = create_sub(api, member).json()
    page = api.c.get("/api/subscriptions", params={"member_id": member}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == s["id"]
    pays = api.c.get("/api/payments", params={"subscription_id": s["id"]}).json()
    assert pays["total"] == 1 and pays["items"][0]["payment_method_code"] == "CASH"
    api.c.post(f"/api/payments/{s['payments'][0]['id']}/void", json={"reason": "test"}, headers=api.h)
    assert api.c.get("/api/payments", params={"member_id": member, "status": "VOIDED"}).json()["total"] == 1
    assert api.c.get("/api/payments", params={"member_id": member, "status": "CONFIRMED"}).json()["total"] == 0
    assert api.c.get("/api/payments", params={"status": "bogus"}).status_code == 422
    assert api.c.get("/api/subscriptions", params={"limit": 1000}).status_code == 422
