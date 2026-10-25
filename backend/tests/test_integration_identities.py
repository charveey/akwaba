"""Intégration lot 9b : verdicts par identité (PostgreSQL réel, horloge contrôlée, vraie ingestion)."""
import os
import pathlib
import random
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
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
from app.schemas.agent import BatchIn
from app.services import agent_admin
from app.services import protected as protected_svc
from app.services.agent_auth import AgentContext
from app.services.ingest import ingest_batch

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent
PASSWORD = "correct-horse-battery"
HASH = hash_password(PASSWORD)
NOW = datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc)
BASE = "/api/identities"


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
def ag(engine):
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = maker()
        try:
            yield db
        finally:
            db.close()

    clk = [NOW]
    app = create_app()
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_clock] = lambda: FixedClock(clk[0])
    name = f"n-{uuid.uuid4().hex[:8]}"
    with maker() as db:
        agent_admin.create_agent(db, name=name, ip="100.85.214.5", rotate=False, now=NOW)
    with engine.connect() as c:
        node_id = c.execute(text("SELECT id FROM exit_nodes WHERE name = :n"), {"n": name}).scalar_one()
    with TestClient(app, base_url="https://testserver") as c:
        email = f"{uuid.uuid4().hex[:10]}@example.test"
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO admin_users (email, password_hash) VALUES (:e, :h)"), {"e": email, "h": HASH})
        r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200
        ns = SimpleNamespace(c=c, h={"X-CSRF-Token": r.json()["csrf_token"]}, maker=maker, engine=engine,
                             clk=clk, node_id=node_id, name=name)
        ns.plans = {p["code"]: p["id"] for p in c.get("/api/plans").json()}
        ns.methods = {m["code"]: m["id"] for m in c.get("/api/payment-methods").json()}
        yield ns


def login() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.test"


def ip() -> str:
    return f"100.{random.randint(64, 127)}.{random.randint(0, 255)}.{random.randint(1, 254)}"


def obs(lg, confirmed=True, status="RESOLVED"):
    return {
        "src_ip": ip(), "total_flows": 10, "recent_flows": 8, "recent_replied_flows": 6, "unknown_age_flows": 1,
        "new_flows": 2, "active_window": confirmed, "consecutive_active_windows": 2 if confirmed else 0,
        "active_confirmed": confirmed, "identity_status": status, "identity_technical_error": status == "UNRESOLVED",
        "identity": None if status == "UNRESOLVED" else
        {"login_name": lg, "tailscale_user_id": 7, "stable_id": "n" + uuid.uuid4().hex[:8], "is_sharee": True},
        "peer": None,
    }


def ingest(ag, observations, at=None):
    at = at or NOW - timedelta(seconds=60)
    batch = BatchIn.model_validate({
        "batch_id": str(uuid.uuid4()), "schema": 1, "sampled_at": at.isoformat().replace("+00:00", "Z"),
        "window_seconds": 60, "conntrack_ok": True, "status_ok": True, "whois_ok": True, "observations": observations})
    ctx = AgentContext(exit_node_id=ag.node_id, exit_node_name=ag.name, key_id="agt_test", body=b"", now=NOW.timestamp())
    with ag.maker() as db:
        ingest_batch(db, ctx, FixedClock(NOW), batch)


def one(ag, lg):
    page = ag.c.get(BASE, params={"q": lg}).json()
    found = [i for i in page["items"] if i["login_name"] == lg]
    assert len(found) == 1, page
    return found[0]


def member_with(ag, lg, plan=None, created_on=None, paid=True):
    m = ag.c.post("/api/members", json={"full_name": f"Client {lg[:13]}"}, headers=ag.h).json()["id"]
    assert ag.c.post(f"/api/members/{m}/identities", json={"login_name": lg}, headers=ag.h).status_code == 201
    if plan:
        ag.clk[0] = created_on or NOW
        body = {"member_id": m, "plan_id": ag.plans[plan]}
        if paid and plan != "PLATINUM":
            body["payment"] = {"payment_method_id": ag.methods["CASH"]}
        r = ag.c.post("/api/subscriptions", json=body, headers=ag.h)
        ag.clk[0] = NOW
        assert r.status_code == 201, r.text
    return m


def test_the_endpoint_requires_authentication(engine, ag):
    with TestClient(create_app(), base_url="https://testserver") as anon:
        assert anon.get(BASE).status_code == 401


def test_an_unknown_confirmed_identity_is_an_unknown_identity(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    e = one(ag, lg)
    assert (e["verdict"], e["reason"], e["active_now"], e["member"], e["protected"], e["access"]) == (
        "UNKNOWN_IDENTITY", "no_member", True, None, False, None)


def test_unconfirmed_activity_gives_no_decision(ag):
    lg = login()
    ingest(ag, [obs(lg, confirmed=False)])
    e = one(ag, lg)
    assert (e["verdict"], e["reason"], e["active_now"], e["last_confirmed_activity_at"]) == (
        "NO_DECISION", "activity_not_confirmed", False, None)


def test_an_old_confirmation_is_not_activity_now(ag):
    lg = login()
    ingest(ag, [obs(lg)], at=NOW - timedelta(minutes=6))
    e = one(ag, lg)
    assert (e["verdict"], e["active_now"]) == ("NO_DECISION", False) and e["last_confirmed_activity_at"]
    ingest(ag, [obs(lg)], at=NOW - timedelta(minutes=4))
    assert one(ag, lg)["verdict"] == "UNKNOWN_IDENTITY"


def test_a_protected_identity_is_protected_and_the_protection_can_be_removed(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    with ag.maker() as db:
        out = protected_svc.add_protected(db, login=lg, reason=None, admin_id=None, label="cli")
    e = one(ag, lg)
    assert (e["verdict"], e["reason"], e["protected"]) == ("PROTECTED", "protected", True)
    with ag.maker() as db:
        protected_svc.remove_protected(db, protected_id=out.id, now=NOW, admin_id=None, label="cli")
    assert one(ag, lg)["verdict"] == "UNKNOWN_IDENTITY"


def test_an_unconfirmed_protected_identity_gives_no_decision(ag):
    lg = login()
    ingest(ag, [obs(lg, confirmed=False)])
    with ag.maker() as db:
        protected_svc.add_protected(db, login=lg, reason=None, admin_id=None, label="cli")
    assert one(ag, lg)["verdict"] == "NO_DECISION"


@pytest.mark.parametrize("plan, created_on, paid, verdict, reason, access", [
    ("M1", datetime(2039, 12, 20, 12, tzinfo=timezone.utc), True, "OK", "access_active", "ACTIVE"),
    ("M1", datetime(2039, 12, 1, 12, tzinfo=timezone.utc), True, "OK_IN_GRACE", "in_grace", "GRACE_PERIOD"),
    ("M1", datetime(2039, 6, 1, 12, tzinfo=timezone.utc), True, "EXPIRED_CANDIDATE", "expired_after_grace", "EXPIRED"),
    ("M1", datetime(2039, 12, 20, 12, tzinfo=timezone.utc), False, "MANUAL_REVIEW", "no_usable_subscription", "NONE"),
    ("PLATINUM", None, True, "OK", "access_active", "ACTIVE"),
    (None, None, True, "MANUAL_REVIEW", "no_subscription", None),
])
def test_linked_member_verdicts(ag, plan, created_on, paid, verdict, reason, access):
    lg = login()
    m = member_with(ag, lg, plan, created_on, paid)
    ingest(ag, [obs(lg)])
    e = one(ag, lg)
    assert (e["verdict"], e["reason"], e["access"]) == (verdict, reason, access)
    assert e["member"]["id"] == m and e["client_identity_id"]


def test_a_cancelled_subscription_is_an_expired_candidate_immediately(ag):
    lg = login()
    m = member_with(ag, lg, "M1", datetime(2039, 12, 20, 12, tzinfo=timezone.utc))
    ingest(ag, [obs(lg)])
    assert one(ag, lg)["verdict"] == "OK"
    sub = ag.c.get("/api/subscriptions", params={"member_id": m}).json()["items"][0]
    assert ag.c.post(f"/api/subscriptions/{sub['id']}/cancel", headers=ag.h).status_code == 200
    assert one(ag, lg)["verdict"] == "EXPIRED_CANDIDATE"


def test_unlinking_an_identity_makes_it_unknown_again(ag):
    lg = login()
    m = member_with(ag, lg, "M1", datetime(2039, 12, 20, 12, tzinfo=timezone.utc))
    ingest(ag, [obs(lg)])
    assert one(ag, lg)["verdict"] == "OK"
    ident = ag.c.get(f"/api/members/{m}").json()["identities"][0]
    assert ag.c.post(f"/api/members/{m}/identities/{ident['id']}/unlink", headers=ag.h).status_code == 200
    e = one(ag, lg)
    assert (e["verdict"], e["member"], e["client_identity_id"]) == ("UNKNOWN_IDENTITY", None, None)


def test_filters_counts_and_pagination(ag):
    a, b, c = login(), login(), login()
    ingest(ag, [obs(a), obs(b, confirmed=False), obs(c)])
    unknown = ag.c.get(BASE, params={"verdict": "UNKNOWN_IDENTITY", "limit": 200}).json()
    assert {a, c} <= {i["login_name"] for i in unknown["items"]} and b not in {i["login_name"] for i in unknown["items"]}
    assert all(i["verdict"] == "UNKNOWN_IDENTITY" for i in unknown["items"])
    inactive = ag.c.get(BASE, params={"active_now": False, "limit": 200}).json()
    assert b in {i["login_name"] for i in inactive["items"]} and a not in {i["login_name"] for i in inactive["items"]}
    everything = ag.c.get(BASE, params={"limit": 200}).json()
    assert sum(everything["counts"].values()) == everything["total"]
    assert everything["counts"]["UNKNOWN_IDENTITY"] >= 2 and everything["counts"]["NO_DECISION"] >= 1
    only_unknown = ag.c.get(BASE, params={"verdict": "UNKNOWN_IDENTITY"}).json()
    assert only_unknown["counts"] == everything["counts"] and only_unknown["total"] == everything["counts"]["UNKNOWN_IDENTITY"]
    first = ag.c.get(BASE, params={"limit": 1, "offset": 0}).json()
    second = ag.c.get(BASE, params={"limit": 1, "offset": 1}).json()
    assert len(first["items"]) == 1 and first["items"][0]["id"] != second["items"][0]["id"]
    assert first["total"] == everything["total"] and first["recency_seconds"] == 300


def test_search_is_case_insensitive_and_wildcards_are_literal(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    assert one(ag, lg)["login_name"] == lg
    assert ag.c.get(BASE, params={"q": lg.upper()}).json()["total"] == 1
    assert ag.c.get(BASE, params={"q": "%"}).json()["total"] == 0
    assert ag.c.get(BASE, params={"q": "_"}).json()["total"] == 0


def test_unresolved_sources_are_counted_not_listed(ag):
    before = ag.c.get(BASE).json()["unresolved_recent_sources"]
    ingest(ag, [obs(None, confirmed=False, status="UNRESOLVED")])
    page = ag.c.get(BASE).json()
    assert page["unresolved_recent_sources"] == before + 1
    ingest(ag, [obs(None, confirmed=False, status="UNRESOLVED")], at=NOW - timedelta(minutes=10))
    assert ag.c.get(BASE).json()["unresolved_recent_sources"] == before + 1      # trop ancien : non compté


def test_invalid_parameters_are_rejected(ag):
    for params in ({"verdict": "WOULD_BLOCK"}, {"limit": 0}, {"limit": 201}, {"offset": -1},
                   {"active_now": "peutetre"}, {"q": "x" * 101}):
        assert ag.c.get(BASE, params=params).status_code == 422, params


def test_the_endpoint_writes_nothing(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    tables = ("exit_node_identities", "client_identities", "quarantine_records", "enforcement_decisions",
              "protected_identities", "audit_logs")

    def snapshot():
        with ag.engine.connect() as c:
            return [c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in tables]

    before = snapshot()
    for _ in range(3):
        assert ag.c.get(BASE, params={"limit": 200}).status_code == 200
    assert snapshot() == before
