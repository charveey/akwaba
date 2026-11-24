"""Phase 11b — vues de lecture : statuts métier, résumé membre, filtres, tris, totaux, migration 0011.

PostgreSQL réel, horloge contrôlée. Aucune écriture nouvelle : seuls les endpoints existants écrivent.
"""
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
ORIGIN = {"Origin": "https://testserver"}


def alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": URL}
    try:
        subprocess.run(["alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        pytest.fail(f"alembic {' '.join(args)} a échoué:\n{exc.stderr}")


@pytest.fixture
def engine():
    """Base neuve à chaque test : les suppressions de paiements, d'audit et de décisions sont
    interdites par trigger (historique protégé), donc on repart des migrations."""
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
    with TestClient(app, base_url="https://testserver", headers=ORIGIN) as c:
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


def member(api, name, **extra) -> str:
    r = api.c.post("/api/members", json={"full_name": name, **extra}, headers=api.h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def pay(api, method="CASH", **extra):
    return {"payment_method_id": api.methods[method], **extra}


def subscribe(api, member_id, plan="M1", payment=True, **extra) -> dict:
    body = {"member_id": member_id, "plan_id": api.plans[plan], **extra}
    if payment:
        body["payment"] = pay(api) if payment is True else payment
    r = api.c.post("/api/subscriptions", json=body, headers=api.h)
    assert r.status_code == 201, r.text
    return r.json()


def renew(api, sub_id, plan=None, **payment) -> dict:
    body = {"payment": pay(api, **payment)}
    if plan:
        body["plan_id"] = api.plans[plan]
    r = api.c.post(f"/api/subscriptions/{sub_id}/renew", json=body, headers=api.h)
    assert r.status_code == 201, r.text
    return r.json()


def at(y, m, d, hh=10):
    return datetime(y, m, d, hh, tzinfo=timezone.utc).isoformat()


# --- migration 0011 ----------------------------------------------------------------

def test_plan_display_names_after_migration(api):
    plans = {p["code"]: p for p in api.c.get("/api/plans").json()}
    assert {c: p["name"] for c, p in plans.items()} == {
        "M1": "Bronze", "M3": "Silver", "M6": "Gold", "M12": "Diamond", "PLATINUM": "Platinum"}
    assert [plans[c]["duration_months"] for c in ("M1", "M3", "M6", "M12")] == [1, 3, 6, 12]
    assert plans["PLATINUM"]["is_unlimited"] is True
    assert [plans[c]["price_amount"] for c in ("M1", "M3", "M6", "M12")] == [499, 1299, 2599, 5399]


def test_migration_0011_round_trip_keeps_manual_names(engine):
    with engine.begin() as c:
        c.execute(text("UPDATE subscription_plans SET name = 'Gold spécial' WHERE code = 'M6'"))
    alembic("downgrade", "0010")
    with engine.connect() as c:
        names = dict(c.execute(text("SELECT code, name FROM subscription_plans")).all())
    assert names["M1"] == "1 mois" and names["M12"] == "12 mois"
    assert names["M6"] == "Gold spécial"            # jamais écrasé
    alembic("upgrade", "head")
    with engine.connect() as c:
        names = dict(c.execute(text("SELECT code, name FROM subscription_plans")).all())
    assert names["M1"] == "Bronze" and names["M6"] == "Gold spécial"
    with engine.begin() as c:
        c.execute(text("UPDATE subscription_plans SET name = 'Gold' WHERE code = 'M6'"))


# --- statuts métier ------------------------------------------------------------------

def test_payment_kinds_and_plan_change(api):
    m = member(api, "Awa")
    pending = subscribe(api, m, payment=False)
    assert (pending["payment_kind"], pending["plan_changed"]) == ("PENDING", False)

    r = api.c.post("/api/payments", json={"subscription_id": pending["id"], **pay(api)}, headers=api.h)
    assert r.status_code == 201
    first = api.c.get(f"/api/subscriptions/{pending['id']}").json()
    assert first["payment_kind"] == "NEW_MEMBER" and first["expires_on"] == "2026-02-17"

    set_clock(api, 2026, 2, 10)
    on_time = renew(api, first["id"])
    assert (on_time["payment_kind"], on_time["plan_changed"]) == ("RENEWED", False)
    assert on_time["is_current"] is True
    assert api.c.get(f"/api/subscriptions/{first['id']}").json()["is_current"] is False

    # Payé le jour même de expires_on (exclusif) : en retard + changement de niveau.
    set_clock(api, 2026, 3, 17, 18)
    late = renew(api, on_time["id"], plan="M3", paid_at=at(2026, 3, 17, 0))
    assert on_time["expires_on"] == "2026-03-17"
    assert (late["payment_kind"], late["plan_changed"]) == ("LATE_RENEWAL", True)
    assert late["plan_name"] == "Silver"


def test_day_before_expiry_is_renewed(api):
    m = member(api, "Bintou")
    s = subscribe(api, m)                              # expire le 2026-02-17
    set_clock(api, 2026, 2, 16, 23)
    r = renew(api, s["id"], paid_at=at(2026, 2, 16, 23))
    assert r["payment_kind"] == "RENEWED"


def test_resubscription_after_expiry_is_late_with_plan_change(api):
    m = member(api, "Chantal")
    s = subscribe(api, m)                              # M1 : 17/01 → 17/02, grâce 3 jours
    set_clock(api, 2026, 4, 1)                          # expiré, grâce dépassée
    again = subscribe(api, m, plan="M6")
    assert again["previous_subscription_id"] is None
    assert (again["payment_kind"], again["plan_changed"]) == ("LATE_RENEWAL", True)
    assert s["id"] != again["id"]


def test_platinum_is_new_member_and_never_pending(api):
    m = member(api, "Famille")
    s = subscribe(api, m, plan="PLATINUM", payment=False)
    assert (s["payment_status"], s["payment_kind"]) == ("WAIVED", "NEW_MEMBER")


def test_voided_payment_returns_to_pending(api):
    m = member(api, "Djeneba")
    s = subscribe(api, m)
    pid = s["payments"][0]["id"]
    r = api.c.post(f"/api/payments/{pid}/void", json={"reason": "erreur de saisie"}, headers=api.h)
    assert r.status_code == 200
    assert api.c.get(f"/api/subscriptions/{s['id']}").json()["payment_kind"] == "PENDING"


# --- membres : résumé, filtres, tris --------------------------------------------------------

def seed_members(api):
    ids = {}
    ids["actif"] = member(api, "Zoé Active", phone_e164="+2250102030405", country_code="CI")
    subscribe(api, ids["actif"], plan="M3")                  # expire 2026-04-17
    ids["grace"] = member(api, "Yao Grâce")
    subscribe(api, ids["grace"], starts_on="2025-12-16")     # expire 2026-01-16 → grâce le 17/01
    ids["expire"] = member(api, "Xavier Expiré")
    subscribe(api, ids["expire"], starts_on="2025-11-01")    # expire 2025-12-01
    ids["sans"] = member(api, "Willy Sans")
    ids["attente"] = member(api, "Valérie Attente")
    subscribe(api, ids["attente"], plan="M6", payment=False)
    ids["platinum"] = member(api, "Ulysse Platinum")
    subscribe(api, ids["platinum"], plan="PLATINUM", payment=False)
    return ids


def names(page):
    return [m["full_name"] for m in page["items"]]


def test_member_list_has_current_summary(api):
    ids = seed_members(api)
    items = {m["id"]: m for m in api.c.get("/api/members?limit=200").json()["items"]}
    cur = items[ids["actif"]]["current"]
    assert cur["plan_name"] == "Silver" and cur["access_status"] == "ACTIVE"
    assert cur["display_end"] == "2026-04-16" and cur["payment_kind"] == "NEW_MEMBER"
    assert items[ids["grace"]]["current"]["access_status"] == "GRACE_PERIOD"
    assert items[ids["expire"]]["current"]["renewal_status"] == "NON_RENEWAL"
    assert items[ids["sans"]]["current"] is None
    assert items[ids["attente"]]["current"]["payment_kind"] == "PENDING"
    detail = api.c.get(f"/api/members/{ids['actif']}").json()
    assert detail["current"]["subscription_id"] == cur["subscription_id"]


def test_member_filters_apply_before_pagination(api):
    ids = seed_members(api)
    page = api.c.get("/api/members?access_status=NONE&limit=1&sort=name").json()
    assert page["total"] == 2                                 # sans abonnement + en attente
    assert names(page) == ["Valérie Attente"]
    page2 = api.c.get("/api/members?access_status=NONE&limit=1&offset=1&sort=name").json()
    assert names(page2) == ["Willy Sans"]
    assert api.c.get("/api/members?plan_code=M3").json()["total"] == 1
    assert api.c.get("/api/members?access_status=EXPIRED").json()["items"][0]["id"] == ids["expire"]
    assert api.c.get("/api/members?q=zo&access_status=ACTIVE").json()["total"] == 1


def test_member_sorts_are_deterministic(api):
    seed_members(api)
    by_name = names(api.c.get("/api/members?sort=name").json())
    assert by_name == sorted(by_name, key=str.casefold)
    assert names(api.c.get("/api/members?sort=-name").json()) == by_name[::-1]
    expiry = names(api.c.get("/api/members?sort=expiry").json())
    assert expiry[:4] == ["Xavier Expiré", "Yao Grâce", "Zoé Active", "Valérie Attente"]
    assert set(expiry[4:]) == {"Willy Sans", "Ulysse Platinum"}   # sans échéance : à la fin
    assert names(api.c.get("/api/members?sort=expiry").json()) == expiry
    # pages successives = liste complète, sans doublon
    full = names(api.c.get("/api/members?sort=-created&limit=200").json())
    paged = sum((names(api.c.get(f"/api/members?sort=-created&limit=2&offset={o}").json()) for o in (0, 2, 4)), [])
    assert paged == full and len(set(paged)) == 6


def test_member_list_rejects_unknown_values(api):
    assert api.c.get("/api/members?sort=password").status_code == 422
    assert api.c.get("/api/members?access_status=ROOT").status_code == 422


# --- abonnements : filtres ------------------------------------------------------------------

def test_subscription_filters(api):
    ids = seed_members(api)
    r = api.c.get("/api/subscriptions?access_status=GRACE_PERIOD").json()
    assert [s["member_id"] for s in r["items"]] == [ids["grace"]] and r["items"][0]["member_name"] == "Yao Grâce"
    assert api.c.get("/api/subscriptions?renewal_status=NON_RENEWAL").json()["total"] == 1
    assert api.c.get("/api/subscriptions?payment_status=PENDING").json()["total"] == 1
    soon = api.c.get("/api/subscriptions?expiring_within_days=90&current_only=true&sort=expiry").json()
    assert [s["member_name"] for s in soon["items"]] == ["Zoé Active"]
    assert api.c.get("/api/subscriptions?payment_kind=NEW_MEMBER").json()["total"] == 4
    paged = api.c.get("/api/subscriptions?payment_kind=NEW_MEMBER&limit=1&offset=3&sort=member").json()
    assert paged["total"] == 4 and len(paged["items"]) == 1
    assert api.c.get("/api/subscriptions?sort=hack").status_code == 422


# --- paiements : filtres, totaux ---------------------------------------------------------------

def seed_payments(api):
    a = member(api, "Alpha")
    s1 = subscribe(api, a, payment=pay(api, "CASH", paid_at=at(2026, 1, 5)))           # 499 EUR-cents → XOF
    set_clock(api, 2026, 2, 20)
    s2 = renew(api, s1["id"], plan="M3", method="WAVE", paid_at=at(2026, 2, 18), settled_amount=8500)
    b = member(api, "Beta")
    s3 = subscribe(api, b, payment=pay(api, "WAVE", paid_at=at(2026, 2, 1)))
    return s1, s2, s3


def test_payments_list_context_and_totals(api):
    s1, s2, s3 = seed_payments(api)
    page = api.c.get("/api/payments").json()
    assert page["total"] == 3
    first = page["items"][0]
    assert first["paid_at"].startswith("2026-02-18") and first["member_name"] == "Alpha"
    assert (first["payment_kind"], first["plan_changed"], first["plan_name"]) == ("LATE_RENEWAL", True, "Silver")
    expected = sum(p["settled_amount"] for p in page["items"])
    assert page["totals"] == {"count": 3, "confirmed_count": 3, "voided_count": 0, "collected_xof": expected}
    assert 8500 in [p["settled_amount"] for p in page["items"]]


def test_payment_totals_cover_all_filtered_rows_not_only_page(api):
    seed_payments(api)
    full = api.c.get("/api/payments").json()["totals"]["collected_xof"]
    one = api.c.get("/api/payments?limit=1").json()
    assert len(one["items"]) == 1 and one["totals"]["collected_xof"] == full and one["total"] == 3


def test_voided_payments_excluded_from_collected(api):
    s1, s2, s3 = seed_payments(api)
    pid = s3["payments"][0]["id"]
    amount = s3["payments"][0]["settled_amount"]
    before = api.c.get("/api/payments").json()["totals"]["collected_xof"]
    assert api.c.post(f"/api/payments/{pid}/void", json={"reason": "doublon"}, headers=api.h).status_code == 200
    t = api.c.get("/api/payments").json()["totals"]
    assert t["collected_xof"] == before - amount and t["voided_count"] == 1 and t["count"] == 3
    assert api.c.get("/api/payments?status=VOIDED").json()["totals"]["collected_xof"] == 0


def test_payment_filters(api):
    seed_payments(api)
    g = lambda q: api.c.get(f"/api/payments?{q}").json()
    assert g("method=WAVE")["total"] == 2
    assert g("date_from=2026-02-01&date_to=2026-02-01")["total"] == 1          # bornes inclusives (UTC)
    assert g("date_from=2026-02-02")["total"] == 1
    assert g("payment_kind=NEW_MEMBER")["total"] == 2
    assert g("plan_changed=true")["total"] == 1
    assert g("payment_kind=LATE_RENEWAL&method=CASH")["total"] == 0
    assert [p["paid_at"][:10] for p in g("sort=paid_at")["items"]] == ["2026-01-05", "2026-02-01", "2026-02-18"]
    assert api.c.get("/api/payments?date_from=2026-03-01&date_to=2026-02-01").status_code == 422


# --- sécurité inchangée ----------------------------------------------------------------------

def test_writes_still_require_csrf_and_origin(api):
    m = member(api, "Sécurité")
    body = {"member_id": m, "plan_id": api.plans["M1"], "payment": pay(api)}
    assert api.c.post("/api/subscriptions", json=body).status_code == 403                      # sans jeton
    assert api.c.post("/api/subscriptions", json=body, headers={**api.h, "Origin": "https://evil.example"}).status_code == 403
    assert api.c.put(f"/api/members/{m}", json={"full_name": "x"}, headers={"Origin": "https://ubuntu.tail14614.ts.net"}).status_code == 403


def test_reads_require_admin(engine):
    with TestClient(create_app(), base_url="https://testserver", headers=ORIGIN) as anon:
        for path in ("/api/members?sort=name", "/api/payments?payment_kind=RENEWED", "/api/subscriptions?current_only=true"):
            assert anon.get(path).status_code == 401


def test_early_renewal_keeps_member_access_from_subscription_in_force(api):
    """Renouvellement anticipé : le nouvel abonnement commence plus tard ; le membre reste ACTIF."""
    m = member(api, "Anticipé")
    s = subscribe(api, m)                                  # 17/01 → expire le 17/02
    set_clock(api, 2026, 2, 1)
    r = renew(api, s["id"], plan="M3")
    assert r["starts_on"] == "2026-02-17" and r["starts_in_future"] is True and r["access_status"] == "NONE"
    cur = api.c.get(f"/api/members/{m}").json()["current"]
    assert cur["subscription_id"] == r["id"] and cur["plan_name"] == "Silver"
    assert cur["access_status"] == "ACTIVE" and cur["upcoming_starts_on"] == "2026-02-17"
    assert api.c.get("/api/members?access_status=ACTIVE").json()["total"] == 1
    set_clock(api, 2026, 2, 17)                            # le renouvellement commence
    cur = api.c.get(f"/api/members/{m}").json()["current"]
    assert cur["access_status"] == "ACTIVE" and cur["upcoming_starts_on"] is None
