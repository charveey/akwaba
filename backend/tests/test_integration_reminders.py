"""Intégration lot 5c : génération, idempotence, statuts, renouvellement (PostgreSQL réel)."""
import os
import pathlib
import subprocess
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
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


def set_clock(api, y, m, d):
    api.app.dependency_overrides[get_clock] = lambda: FixedClock(datetime(y, m, d, 12, 0, tzinfo=timezone.utc))


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
        ns.plans = {p["code"]: p["id"] for p in c.get("/api/plans").json()}
        ns.methods = {m["code"]: m["id"] for m in c.get("/api/payment-methods").json()}
        yield ns


_phone_counter = iter(range(10_000_000, 99_999_999))


def member(api, name="Awa Koné", phone="auto") -> str:
    if phone == "auto":
        phone = f"+2250{next(_phone_counter)}"
    body = {"full_name": name, "phone_e164": phone}
    r = api.c.post("/api/members", json=body, headers=api.h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def subscribe(api, member_id, plan="M1", start=(2026, 1, 17)):
    set_clock(api, *start)
    body = {"member_id": member_id, "plan_id": api.plans[plan]}
    if plan != "PLATINUM":
        body["payment"] = {"payment_method_id": api.methods["CASH"]}
    r = api.c.post("/api/subscriptions", json=body, headers=api.h)
    assert r.status_code == 201, r.text
    return r.json()


def generate(api, y, m, d):
    set_clock(api, y, m, d)
    r = api.c.post("/api/reminders/generate", headers=api.h)
    assert r.status_code == 200, r.text
    return r.json()


def audit_rows(engine, action):
    with engine.connect() as c:
        return c.execute(text("SELECT details, entity_id FROM audit_logs WHERE action = :a"), {"a": action}).all()


def test_endpoints_require_authentication_and_csrf(engine, api):
    with TestClient(create_app(), base_url="https://testserver") as anon:
        assert anon.get("/api/reminders").status_code == 401
        assert anon.post("/api/reminders/generate").status_code == 401
    assert api.c.post("/api/reminders/generate").status_code == 403
    assert api.c.post(f"/api/reminders/{uuid.uuid4()}/open").status_code == 403


def test_generates_the_right_reminder_each_day_with_message_and_no_catch_up(api):
    m = member(api, "Awa Koné")
    sub = subscribe(api, m)                          # expire le 2026-02-17
    assert generate(api, 2026, 2, 9)["created"] == 0
    res = generate(api, 2026, 2, 10)                 # J-7
    assert res["created"] == 1
    rem = res["reminders"][0]
    assert (rem["offset_days"], rem["due_date"], rem["status"], rem["member_name"]) == (-7, "2026-02-10", "PENDING", "Awa Koné")
    assert rem["message"] == "Bonjour Awa, votre abonnement expire le 17 février 2026 (dans 7 jours). Pensez à le renouveler."
    assert generate(api, 2026, 2, 11)["created"] == 0   # pas de rattrapage
    for day, offset in ((14, -3), (15, -2), (16, -1), (17, 0), (18, 1)):
        r = generate(api, 2026, 2, day)
        assert [x["offset_days"] for x in r["reminders"] if x["subscription_id"] == sub["id"]] == [offset]
    assert generate(api, 2026, 2, 19)["created"] == 0
    listed = api.c.get("/api/reminders", params={"member_id": m}).json()
    assert listed["total"] == 6


def test_generation_is_idempotent(api, engine):
    sub = subscribe(api, member(api))
    first = generate(api, 2026, 2, 17)
    again = generate(api, 2026, 2, 17)
    assert first["created"] >= 1 and again["created"] == 0
    assert again["skipped_existing"] >= 1
    rows = api.c.get("/api/reminders", params={"due_on": "2026-02-17"}).json()["items"]
    assert len([r for r in rows if r["subscription_id"] == sub["id"]]) == 1
    assert any(d["created"] == first["created"] for d, _ in audit_rows(engine, "reminders.generated"))


def test_platinum_unpaid_cancelled_archived_and_phoneless_are_excluded(api):
    plat = subscribe(api, member(api), "PLATINUM", start=(2026, 1, 17))
    set_clock(api, 2026, 1, 17)
    unpaid = api.c.post("/api/subscriptions", headers=api.h,
                        json={"member_id": member(api), "plan_id": api.plans["M1"]}).json()
    cancelled = subscribe(api, member(api))
    api.c.post(f"/api/subscriptions/{cancelled['id']}/cancel", headers=api.h)
    archived_member = member(api)
    archived = subscribe(api, archived_member)
    api.c.post(f"/api/members/{archived_member}/archive", headers=api.h)
    no_phone = subscribe(api, member(api, phone=None))
    ineligible = {plat["id"], unpaid["id"], cancelled["id"], archived["id"], no_phone["id"]}
    generate(api, 2026, 2, 17)
    rows = api.c.get("/api/reminders", params={"due_on": "2026-02-17", "limit": 200}).json()["items"]
    assert not ineligible & {r["subscription_id"] for r in rows}


def test_open_returns_wa_link_and_mark_sent_is_final(api, engine):
    sub = subscribe(api, member(api, "Awa Koné", phone="+225 01 02 03 04 05"))
    rem = next(r for r in generate(api, 2026, 2, 17)["reminders"] if r["subscription_id"] == sub["id"])
    r = api.c.post(f"/api/reminders/{rem['id']}/open", headers=api.h)
    assert r.status_code == 200
    body = r.json()
    assert body["reminder"]["status"] == "OPENED" and body["reminder"]["opened_at"]
    url = urlparse(body["whatsapp_url"])
    assert (url.netloc, url.path) == ("wa.me", "/2250102030405")
    assert "expire aujourd'hui" in parse_qs(url.query)["text"][0]
    again = api.c.post(f"/api/reminders/{rem['id']}/open", headers=api.h)
    assert again.status_code == 200 and again.json()["reminder"]["opened_at"] == body["reminder"]["opened_at"]
    sent = api.c.post(f"/api/reminders/{rem['id']}/mark-sent", headers=api.h)
    assert sent.status_code == 200 and sent.json()["status"] == "SENT" and sent.json()["sent_at"]
    assert api.c.post(f"/api/reminders/{rem['id']}/mark-sent", headers=api.h).status_code == 409
    assert api.c.post(f"/api/reminders/{rem['id']}/open", headers=api.h).status_code == 409
    assert api.c.post(f"/api/reminders/{uuid.uuid4()}/open", headers=api.h).status_code == 404
    with engine.connect() as c:
        audit = c.execute(text("SELECT action, details FROM audit_logs WHERE entity_id = :i ORDER BY id"),
                          {"i": rem["id"]}).all()
    assert [a for a, _ in audit] == ["reminder.opened", "reminder.sent"]
    assert "2250102030405" not in str(audit) and "Bonjour" not in str(audit)


def test_mark_sent_straight_from_pending_sets_opened_at(api):
    sub = subscribe(api, member(api))
    rem = next(r for r in generate(api, 2026, 2, 16)["reminders"] if r["subscription_id"] == sub["id"])
    sent = api.c.post(f"/api/reminders/{rem['id']}/mark-sent", headers=api.h).json()
    assert sent["status"] == "SENT" and sent["opened_at"] == sent["sent_at"]


def test_renewal_cancels_pending_reminders_and_blocks_actions(api):
    sub = subscribe(api, member(api))
    rem = next(r for r in generate(api, 2026, 2, 17)["reminders"] if r["subscription_id"] == sub["id"])
    set_clock(api, 2026, 2, 17)
    renew = api.c.post(f"/api/subscriptions/{sub['id']}/renew", headers=api.h,
                       json={"payment": {"payment_method_id": api.methods["CASH"]}})
    assert renew.status_code == 201
    listed = {r["id"]: r for r in api.c.get("/api/reminders", params={"member_id": sub["member_id"]}).json()["items"]}
    assert listed[rem["id"]]["status"] == "CANCELED"
    assert api.c.post(f"/api/reminders/{rem['id']}/open", headers=api.h).status_code == 409
    assert api.c.post(f"/api/reminders/{rem['id']}/mark-sent", headers=api.h).status_code == 409
    canceled = api.c.get("/api/reminders", params={"member_id": sub["member_id"], "status": "CANCELED"}).json()
    assert canceled["total"] == 1


def test_list_filters_and_validation(api):
    m = member(api)
    subscribe(api, m)
    generate(api, 2026, 2, 17)
    assert api.c.get("/api/reminders", params={"member_id": m, "status": "PENDING"}).json()["total"] == 1
    assert api.c.get("/api/reminders", params={"member_id": m, "status": "SENT"}).json()["total"] == 0
    assert api.c.get("/api/reminders", params={"status": "bogus"}).status_code == 422
    assert api.c.get("/api/reminders", params={"limit": 1000}).status_code == 422
    assert api.c.get("/api/reminders", params={"due_on": "pas-une-date"}).status_code == 422


def test_database_constraints(api, engine):
    m = member(api)
    sub = subscribe(api, m)

    def raw(offset=0, phone="+2250102030405", status="PENDING", opened="NULL", sent="NULL"):
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO reminders (subscription_id, member_id, offset_days, due_date, phone_e164, message, "
                f"status, opened_at, sent_at) VALUES (:s, :m, :o, '2026-02-17', :p, 'x', :st, {opened}, {sent})"),
                {"s": sub["id"], "m": m, "o": offset, "p": phone, "st": status})

    raw(offset=-7)
    with pytest.raises(IntegrityError):
        raw(offset=-7)                                   # un seul rappel par abonnement et offset
    with pytest.raises(IntegrityError):
        raw(offset=5)                                    # offset hors calendrier
    with pytest.raises(IntegrityError):
        raw(offset=-3, phone="0102030405")               # numéro non E.164
    with pytest.raises(IntegrityError):
        raw(offset=-2, status="SENT")                    # SENT exige sent_at
    with pytest.raises(IntegrityError):
        raw(offset=-1, status="OPENED")                  # OPENED exige opened_at
