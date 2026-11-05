"""Intégration lot 10c : routes admin des dossiers, décisions et exemptions (PostgreSQL réel)."""
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
from app.services import quarantine as svc
from app.services.agent_auth import AgentContext
from app.services.ingest import ingest_batch

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent
PASSWORD = "correct-horse-battery"
HASH = hash_password(PASSWORD)
NOW = datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc)
H24 = timedelta(hours=24)
Q, D, E = "/api/quarantine", "/api/enforcement-decisions", "/api/identity-exemptions"


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
            admin_id = conn.execute(
                text("INSERT INTO admin_users (email, password_hash) VALUES (:e, :h) RETURNING id"),
                {"e": email, "h": HASH}).scalar_one()
        r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200
        yield SimpleNamespace(c=c, h={"X-CSRF-Token": r.json()["csrf_token"]}, maker=maker, engine=engine,
                              clk=clk, node_id=node_id, name=name, admin_id=admin_id)


def login() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.test"


def obs(lg):
    return {
        "src_ip": f"100.{random.randint(64, 127)}.{random.randint(0, 255)}.{random.randint(1, 254)}",
        "total_flows": 10, "recent_flows": 8, "recent_replied_flows": 6, "unknown_age_flows": 1, "new_flows": 2,
        "active_window": True, "consecutive_active_windows": 2, "active_confirmed": True,
        "identity_status": "RESOLVED", "identity_technical_error": False,
        "identity": {"login_name": lg, "tailscale_user_id": 7, "stable_id": "n" + uuid.uuid4().hex[:8],
                     "is_sharee": True},
        "peer": None,
    }


def ingest(ag, lg):
    now = ag.clk[0]
    batch = BatchIn.model_validate({
        "batch_id": str(uuid.uuid4()), "schema": 1,
        "sampled_at": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
        "window_seconds": 60, "conntrack_ok": True, "status_ok": True, "whois_ok": True, "observations": [obs(lg)]})
    ctx = AgentContext(exit_node_id=ag.node_id, exit_node_name=ag.name, key_id="agt_test", body=b"", now=now.timestamp())
    with ag.maker() as db:
        ingest_batch(db, ctx, FixedClock(now), batch)


def heartbeat(ag):
    with ag.engine.begin() as c:
        c.execute(text("INSERT INTO agent_heartbeats (exit_node_id, received_at, agent_version, protocol_version, "
                       "conntrack_ok, status_ok, whois_ok, bytes_available, spool_depth) "
                       "VALUES (:n, :t, 'test', 1, true, true, true, false, 0)"), {"n": ag.node_id, "t": ag.clk[0]})


def run(ag):
    with ag.maker() as db:
        return svc.reconcile(db, FixedClock(ag.clk[0]))


def rows(ag, sql, **params):
    with ag.engine.connect() as c:
        return c.execute(text(sql), params).all()


def pending(ag, lg=None):
    """Une identité inconnue active, avec un dossier PENDING. Retourne (login, id du dossier)."""
    lg = lg or login()
    ingest(ag, lg)
    run(ag)
    rid = rows(ag, "SELECT r.id FROM quarantine_records r JOIN exit_node_identities i ON i.id = r.exit_node_identity_id "
                   "WHERE i.login_name = :l ORDER BY r.started_at DESC LIMIT 1", l=lg)[0][0]
    return lg, rid


def blocked(ag):
    lg, rid = pending(ag)
    ag.clk[0] = NOW + H24
    heartbeat(ag)
    run(ag)
    return lg, rid


def test_every_route_requires_authentication_and_the_actions_require_csrf(engine, ag):
    with TestClient(create_app(), base_url="https://testserver") as anon:
        for path in (Q, f"{Q}/{uuid.uuid4()}", D, E):
            assert anon.get(path).status_code == 401, path
    for path in (f"{Q}/{uuid.uuid4()}/authorize", f"{Q}/{uuid.uuid4()}/dismiss", f"{E}/{uuid.uuid4()}/end"):
        assert ag.c.post(path).status_code == 403, path


def test_list_and_detail_of_a_record(ag):
    lg, rid = pending(ag)
    page = ag.c.get(Q, params={"q": lg}).json()
    [item] = page["items"]
    assert (item["id"], item["login_name"], item["status"], item["exempt"], item["simulated_block"]) == (
        str(rid), lg, "PENDING", False, False)
    assert page["counts"] == {"PENDING": 1} and page["total"] == 1
    detail = ag.c.get(f"{Q}/{rid}").json()
    assert detail["record"]["id"] == str(rid) and detail["decisions"] == []
    assert ag.c.get(f"{Q}/{uuid.uuid4()}").status_code == 404


def test_filters_search_and_pagination(ag):
    a, _ = pending(ag)
    b, _ = pending(ag)
    both = ag.c.get(Q, params={"limit": 200}).json()
    assert {a, b} <= {i["login_name"] for i in both["items"]} and sum(both["counts"].values()) >= 2
    assert ag.c.get(Q, params={"status": "EXPIRED", "q": a}).json()["total"] == 0
    assert ag.c.get(Q, params={"status": "PENDING", "q": a}).json()["total"] == 1
    assert ag.c.get(Q, params={"open_only": True, "q": b}).json()["total"] == 1
    assert ag.c.get(Q, params={"q": "%"}).json()["total"] == 0
    first = ag.c.get(Q, params={"limit": 1, "offset": 0}).json()
    second = ag.c.get(Q, params={"limit": 1, "offset": 1}).json()
    assert first["items"][0]["id"] != second["items"][0]["id"] and first["total"] == both["total"]


def test_authorizing_through_the_api_resolves_the_record_and_creates_an_exemption(ag):
    lg, rid = pending(ag)
    r = ag.c.post(f"{Q}/{rid}/authorize", json={"note": "  invité  "}, headers=ag.h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["resolved_by_admin_id"], body["resolution_note"], body["exempt"]) == (
        "AUTHORIZED", str(ag.admin_id), "invité", True)
    ex = ag.c.get(E, params={"active": True, "q": lg}).json()
    assert ex["total"] == 1 and ex["items"][0]["active"] is True and ex["items"][0]["quarantine_record_id"] == str(rid)
    assert ag.c.post(f"{Q}/{rid}/authorize", headers=ag.h).status_code == 409
    assert ag.c.post(f"{Q}/{rid}/dismiss", headers=ag.h).status_code == 409
    audit = rows(ag, "SELECT action, actor_admin_id, details FROM audit_logs WHERE entity_id = :i", i=str(rid))
    assert [(a, by) for a, by, _ in audit][-1] == ("quarantine.authorized", ag.admin_id)
    assert "invité" not in str(audit)


def test_dismissing_through_the_api_creates_no_exemption(ag):
    lg, rid = pending(ag)
    r = ag.c.post(f"{Q}/{rid}/dismiss", headers=ag.h)         # corps absent : le motif est facultatif
    assert r.status_code == 200 and r.json()["status"] == "DISMISSED" and r.json()["exempt"] is False
    assert ag.c.get(E, params={"q": lg}).json()["total"] == 0


def test_authorizing_a_blocked_record_logs_one_unblock_in_the_decisions(ag):
    lg, rid = blocked(ag)
    assert ag.c.get(f"{Q}/{rid}").json()["record"]["simulated_block"] is True
    assert ag.c.post(f"{Q}/{rid}/authorize", headers=ag.h).status_code == 200
    page = ag.c.get(D, params={"q": lg}).json()
    assert [d["decision"] for d in page["items"]] == ["WOULD_UNBLOCK", "WOULD_BLOCK"]
    assert all(d["mode"] == "dry_run" and d["enforced"] is False and d["login_name"] == lg for d in page["items"])
    detail = ag.c.get(f"{Q}/{rid}").json()
    assert [d["decision"] for d in detail["decisions"]] == ["WOULD_UNBLOCK", "WOULD_BLOCK"]
    assert detail["record"]["simulated_block"] is False


def test_decision_filters(ag):
    lg, _ = blocked(ag)
    only_block = ag.c.get(D, params={"q": lg, "decision": "WOULD_BLOCK"}).json()
    assert only_block["total"] == 1 and only_block["items"][0]["reason"] == "window_elapsed"
    assert ag.c.get(D, params={"q": lg, "decision": "WOULD_UNBLOCK"}).json()["total"] == 0
    day = (NOW + H24).date().isoformat()
    assert ag.c.get(D, params={"q": lg, "date_from": day, "date_to": day}).json()["total"] == 1
    assert ag.c.get(D, params={"q": lg, "date_from": "2040-01-03"}).json()["total"] == 0
    assert ag.c.get(D, params={"q": lg, "date_to": "2040-01-01"}).json()["total"] == 0
    assert ag.c.get(D, params={"date_from": "2040-02-01", "date_to": "2040-01-01"}).status_code == 422
    assert ag.c.get(D, params={"limit": 1}).json()["limit"] == 1


def test_ending_an_exemption_through_the_api(ag):
    lg, rid = pending(ag)
    ag.c.post(f"{Q}/{rid}/authorize", headers=ag.h)
    [item] = ag.c.get(E, params={"active": True, "q": lg}).json()["items"]
    r = ag.c.post(f"{E}/{item['id']}/end", headers=ag.h)
    assert r.status_code == 200
    assert (r.json()["active"], r.json()["ended_by_admin_id"]) == (False, str(ag.admin_id))
    assert ag.c.post(f"{E}/{item['id']}/end", headers=ag.h).status_code == 409
    assert ag.c.post(f"{E}/{uuid.uuid4()}/end", headers=ag.h).status_code == 404
    assert ag.c.get(E, params={"active": True, "q": lg}).json()["total"] == 0
    assert ag.c.get(E, params={"active": False, "q": lg}).json()["total"] == 1
    assert ag.c.get(E, params={"q": lg}).json()["total"] == 1


def test_validation(ag):
    lg, rid = pending(ag)
    assert ag.c.get(Q, params={"status": "WOULD_BLOCK"}).status_code == 422
    assert ag.c.get(Q, params={"limit": 0}).status_code == 422
    assert ag.c.get(Q, params={"limit": 201}).status_code == 422
    assert ag.c.get(D, params={"decision": "PENDING"}).status_code == 422
    assert ag.c.get(E, params={"active": "peutetre"}).status_code == 422
    assert ag.c.get(f"{Q}/pas-un-uuid").status_code == 422
    for body in ({"note": "x" * 501}, {"note": 5}, {"bonus": 1}):
        assert ag.c.post(f"{Q}/{rid}/authorize", json=body, headers=ag.h).status_code == 422, body
    assert ag.c.post(f"{Q}/{uuid.uuid4()}/authorize", headers=ag.h).status_code == 404
    assert statuses_of(ag, rid) == "PENDING"


def statuses_of(ag, rid):
    return rows(ag, "SELECT status FROM quarantine_records WHERE id = :r", r=rid)[0][0]


def test_the_read_routes_write_nothing(ag):
    lg, rid = blocked(ag)
    tables = ("quarantine_records", "enforcement_decisions", "identity_exemptions", "audit_logs", "job_runs")

    def snapshot():
        return [rows(ag, f"SELECT count(*) FROM {t}")[0][0] for t in tables]

    before = snapshot()
    for path in (Q, f"{Q}/{rid}", D, E):
        assert ag.c.get(path, params={"limit": 200} if path != f"{Q}/{rid}" else None).status_code == 200
    assert snapshot() == before
