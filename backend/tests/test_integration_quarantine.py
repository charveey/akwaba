"""Intégration lot 10b : réconciliation, décisions dry-run, exemptions (PostgreSQL réel, horloge contrôlée)."""
import os
import pathlib
import random
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_clock
from app.core.security import hash_password
from app.db.session import get_db
from app.domain.clock import FixedClock
from app.main import create_app
from app.schemas.agent import BatchIn
from app.services import agent_admin
from app.services import protected as protected_svc
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
        ns = SimpleNamespace(c=c, h={"X-CSRF-Token": r.json()["csrf_token"]}, maker=maker, engine=engine,
                             clk=clk, node_id=node_id, name=name, admin_id=admin_id)
        ns.plans = {p["code"]: p["id"] for p in c.get("/api/plans").json()}
        ns.methods = {m["code"]: m["id"] for m in c.get("/api/payment-methods").json()}
        yield ns


# ------------------------------------------------------------------- aides

def login() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.test"


def obs(lg, confirmed=True):
    return {
        "src_ip": f"100.{random.randint(64, 127)}.{random.randint(0, 255)}.{random.randint(1, 254)}",
        "total_flows": 10, "recent_flows": 8, "recent_replied_flows": 6, "unknown_age_flows": 1, "new_flows": 2,
        "active_window": confirmed, "consecutive_active_windows": 2 if confirmed else 0, "active_confirmed": confirmed,
        "identity_status": "RESOLVED", "identity_technical_error": False,
        "identity": {"login_name": lg, "tailscale_user_id": 7, "stable_id": "n" + uuid.uuid4().hex[:8],
                     "is_sharee": True},
        "peer": None,
    }


def ingest(ag, observations):
    now = ag.clk[0]
    batch = BatchIn.model_validate({
        "batch_id": str(uuid.uuid4()), "schema": 1,
        "sampled_at": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
        "window_seconds": 60, "conntrack_ok": True, "status_ok": True, "whois_ok": True,
        "observations": observations})
    ctx = AgentContext(exit_node_id=ag.node_id, exit_node_name=ag.name, key_id="agt_test", body=b"", now=now.timestamp())
    with ag.maker() as db:
        ingest_batch(db, ctx, FixedClock(now), batch)


def heartbeat(ag, at, conntrack_ok=True, whois_ok=True):
    with ag.engine.begin() as c:
        c.execute(text(
            "INSERT INTO agent_heartbeats (exit_node_id, received_at, agent_version, protocol_version, conntrack_ok, "
            "status_ok, whois_ok, bytes_available, spool_depth) "
            "VALUES (:n, :t, 'test', 1, :c, true, :w, false, 0)"),
            {"n": ag.node_id, "t": at, "c": conntrack_ok, "w": whois_ok})


def run(ag, **kw):
    with ag.maker() as db:
        return svc.reconcile(db, FixedClock(ag.clk[0]), **kw)


def rows(ag, sql, **params):
    with ag.engine.connect() as c:
        return c.execute(text(sql), params).all()


def records(ag, lg):
    return rows(ag, "SELECT r.status, r.started_at, r.expires_at, r.resolved_at, r.resolved_by_admin_id, "
                    "r.resolution_note, r.id FROM quarantine_records r JOIN exit_node_identities i "
                    "ON i.id = r.exit_node_identity_id WHERE i.login_name = :l ORDER BY r.started_at, r.id", l=lg)


def decisions(ag, lg):
    return rows(ag, "SELECT d.decision, d.mode, d.enforced, d.reason FROM enforcement_decisions d "
                    "JOIN exit_node_identities i ON i.id = d.exit_node_identity_id WHERE i.login_name = :l "
                    "ORDER BY d.id", l=lg)


def identity_id(ag, lg):
    return rows(ag, "SELECT id FROM exit_node_identities WHERE login_name = :l", l=lg)[0][0]


def statuses(ag, lg):
    return [r[0] for r in records(ag, lg)]


def member_with(ag, lg, plan="M1", paid=True):
    previous = ag.clk[0]
    m = ag.c.post("/api/members", json={"full_name": f"Client {lg[:13]}"}, headers=ag.h).json()["id"]
    assert ag.c.post(f"/api/members/{m}/identities", json={"login_name": lg}, headers=ag.h).status_code == 201
    body = {"member_id": m, "plan_id": ag.plans[plan]}
    if paid:
        body["payment"] = {"payment_method_id": ag.methods["CASH"]}
    r = ag.c.post("/api/subscriptions", json=body, headers=ag.h)
    assert r.status_code == 201, r.text
    ag.clk[0] = previous
    return m


def blocked_identity(ag, lg=None):
    """Une identité inconnue active, mise en quarantaine puis bloquée (en simulation)."""
    lg = lg or login()
    ingest(ag, [obs(lg)])
    run(ag)
    ag.clk[0] = NOW + H24
    heartbeat(ag, ag.clk[0])
    run(ag)
    assert statuses(ag, lg) == ["EXPIRED"]
    return lg


# ------------------------------------------------------------- ouverture

def test_an_active_unknown_identity_opens_a_pending_record_once(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    [(status, started, expires, resolved, by, note, rid)] = records(ag, lg)
    assert (status, started, expires - started, resolved, by) == ("PENDING", NOW, H24, None, None)
    assert decisions(ag, lg) == []
    audit = rows(ag, "SELECT action, actor_label, details FROM audit_logs WHERE entity_id = :i", i=str(rid))
    assert audit == [("quarantine.opened", "reconciler",
                      {"login_name": lg, "from": None, "to": "PENDING", "decision": None, "reason": "unauthorized_active"})]
    run(ag)
    run(ag)
    assert len(records(ag, lg)) == 1


def test_unconfirmed_protected_and_clean_identities_never_open_a_record(ag):
    quiet, protected, linked = login(), login(), login()
    ingest(ag, [obs(quiet, confirmed=False), obs(protected), obs(linked)])
    with ag.maker() as db:
        protected_svc.add_protected(db, login=protected, reason=None, admin_id=None, label="cli")
    member_with(ag, linked)
    run(ag)
    assert records(ag, quiet) == [] and records(ag, protected) == [] and records(ag, linked) == []


def test_a_pending_record_is_authorized_automatically_when_a_paid_member_is_linked(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    member_with(ag, lg)
    run(ag)
    [(status, _, _, resolved, by, note, _)] = records(ag, lg)
    assert (status, resolved, by, note) == ("AUTHORIZED", NOW, None, "auto_ok")
    assert decisions(ag, lg) == []


# ------------------------------------------------------------- blocage simulé

def test_the_window_elapses_into_one_simulated_block(ag):
    lg = blocked_identity(ag)
    assert decisions(ag, lg) == [("WOULD_BLOCK", "dry_run", False, "window_elapsed")]
    run(ag)
    run(ag)
    assert len(decisions(ag, lg)) == 1 and statuses(ag, lg) == ["EXPIRED"]


def test_nothing_is_blocked_just_before_the_deadline(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    ag.clk[0] = NOW + H24 - timedelta(seconds=1)
    heartbeat(ag, ag.clk[0])
    run(ag)
    assert statuses(ag, lg) == ["PENDING"] and decisions(ag, lg) == []


@pytest.mark.parametrize("beat", [None, "stale", "conntrack_down", "whois_down"])
def test_a_block_is_deferred_while_the_agent_is_unhealthy(ag, beat):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    ag.clk[0] = NOW + H24 + timedelta(hours=1)
    if beat == "stale":
        heartbeat(ag, ag.clk[0] - timedelta(minutes=10))
    elif beat == "conntrack_down":
        heartbeat(ag, ag.clk[0], conntrack_ok=False)
    elif beat == "whois_down":
        heartbeat(ag, ag.clk[0], whois_ok=False)
    report = run(ag)
    assert statuses(ag, lg) == ["PENDING"] and decisions(ag, lg) == [] and report.skipped_unhealthy >= 1
    heartbeat(ag, ag.clk[0])
    run(ag)
    assert statuses(ag, lg) == ["EXPIRED"] and [d[0] for d in decisions(ag, lg)] == ["WOULD_BLOCK"]


def test_a_review_record_never_leads_to_a_block(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    member_with(ag, lg, paid=False)            # paiement en attente : aucun accès exploitable
    run(ag)
    assert statuses(ag, lg) == ["MANUAL_REVIEW"]
    ag.clk[0] = NOW + 3 * H24
    heartbeat(ag, ag.clk[0])
    run(ag)
    assert statuses(ag, lg) == ["MANUAL_REVIEW"] and decisions(ag, lg) == []


def test_the_dry_run_columns_are_enforced_by_the_database(ag):
    lg = blocked_identity(ag)
    with pytest.raises(IntegrityError):
        with ag.engine.begin() as c:
            c.execute(text("INSERT INTO enforcement_decisions (exit_node_identity_id, decision, enforced) "
                           "VALUES (:i, 'WOULD_BLOCK', true)"), {"i": identity_id(ag, lg)})


# ------------------------------------------------------------- levée du blocage

def test_a_paid_member_lifts_the_block_with_exactly_one_unblock(ag):
    lg = blocked_identity(ag)
    member_with(ag, lg)
    heartbeat(ag, ag.clk[0])
    run(ag)
    [(status, _, _, _, by, note, _)] = records(ag, lg)
    assert (status, by, note) == ("AUTHORIZED", None, "auto_ok")
    assert [d[0] for d in decisions(ag, lg)] == ["WOULD_BLOCK", "WOULD_UNBLOCK"]
    run(ag)
    run(ag)
    assert len(decisions(ag, lg)) == 2


def test_a_blocked_identity_that_becomes_a_review_is_unblocked_exactly_once(ag):
    lg = blocked_identity(ag)
    member_with(ag, lg, paid=False)
    heartbeat(ag, ag.clk[0])
    for _ in range(3):
        run(ag)
    assert statuses(ag, lg) == ["MANUAL_REVIEW"]
    assert [d[0] for d in decisions(ag, lg)] == ["WOULD_BLOCK", "WOULD_UNBLOCK"]


def test_an_unblock_is_not_written_without_a_block_in_effect(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    with ag.engine.begin() as c:                              # état incohérent : EXPIRED sans décision
        c.execute(text("UPDATE quarantine_records SET status = 'EXPIRED' WHERE id = :r"), {"r": records(ag, lg)[0][6]})
    member_with(ag, lg)
    report = run(ag)
    assert statuses(ag, lg) == ["AUTHORIZED"] and decisions(ag, lg) == []
    assert report.anomalies.get("unblock_without_block", 0) >= 1


def test_a_block_is_not_duplicated_when_one_is_already_in_effect(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    with ag.engine.begin() as c:
        c.execute(text("INSERT INTO enforcement_decisions (exit_node_identity_id, quarantine_record_id, decision, "
                       "reason) VALUES (:i, :r, 'WOULD_BLOCK', 'manual')"),
                  {"i": identity_id(ag, lg), "r": records(ag, lg)[0][6]})
    ag.clk[0] = NOW + H24
    heartbeat(ag, ag.clk[0])
    report = run(ag)
    assert statuses(ag, lg) == ["EXPIRED"] and [d[3] for d in decisions(ag, lg)] == ["manual"]
    assert report.anomalies.get("block_already_in_effect", 0) >= 1


# ------------------------------------------------- décisions de l'administrateur

def test_authorizing_resolves_the_record_and_creates_an_exemption_that_stops_new_records(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    rid = records(ag, lg)[0][6]
    with ag.maker() as db:
        svc.resolve_record(db, record_id=rid, action="AUTHORIZE", admin_id=ag.admin_id, now=NOW, note="invité")
    [(status, _, _, resolved, by, note, _)] = records(ag, lg)
    assert (status, resolved, by, note) == ("AUTHORIZED", NOW, ag.admin_id, "invité")
    assert rows(ag, "SELECT count(*) FROM identity_exemptions WHERE exit_node_identity_id = :i AND ended_at IS NULL",
                i=identity_id(ag, lg)) == [(1,)]
    ingest(ag, [obs(lg)])
    run(ag)
    assert len(records(ag, lg)) == 1                           # l'exemption empêche tout nouveau dossier


def test_an_exemption_also_covers_an_expired_member_and_ending_it_restores_the_normal_rules(ag):
    lg = login()
    member_with(ag, lg)                                         # abonnement payé aujourd'hui
    ag.clk[0] = NOW + timedelta(days=40)                        # expiré, grâce terminée
    ingest(ag, [obs(lg)])
    run(ag)
    [(status, *_rest, rid)] = records(ag, lg)
    assert status == "PENDING"
    with ag.maker() as db:
        svc.resolve_record(db, record_id=rid, action="AUTHORIZE", admin_id=ag.admin_id, now=ag.clk[0])
    ingest(ag, [obs(lg)])
    run(ag)
    assert len(records(ag, lg)) == 1
    [(eid,)] = rows(ag, "SELECT id FROM identity_exemptions WHERE exit_node_identity_id = :i", i=identity_id(ag, lg))
    with ag.maker() as db:
        svc.end_exemption(db, exemption_id=eid, admin_id=ag.admin_id, now=ag.clk[0])
    ingest(ag, [obs(lg)])
    run(ag)
    assert statuses(ag, lg) == ["AUTHORIZED", "PENDING"]
    audit = rows(ag, "SELECT action, actor_admin_id FROM audit_logs WHERE entity_id = :e", e=str(eid))
    assert audit == [("identity_exemption.ended", ag.admin_id)]


def test_authorizing_a_blocked_record_writes_one_unblock(ag):
    lg = blocked_identity(ag)
    with ag.maker() as db:
        svc.resolve_record(db, record_id=records(ag, lg)[0][6], action="AUTHORIZE", admin_id=ag.admin_id,
                           now=ag.clk[0])
    assert [d[0] for d in decisions(ag, lg)] == ["WOULD_BLOCK", "WOULD_UNBLOCK"]
    assert decisions(ag, lg)[1][3] == "admin_authorized"


def test_dismissing_suppresses_new_records_for_exactly_seven_days_without_an_exemption(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    with ag.maker() as db:
        svc.resolve_record(db, record_id=records(ag, lg)[0][6], action="DISMISS", admin_id=ag.admin_id, now=NOW)
    assert rows(ag, "SELECT count(*) FROM identity_exemptions WHERE exit_node_identity_id = :i",
                i=identity_id(ag, lg)) == [(0,)]
    ag.clk[0] = NOW + timedelta(days=7) - timedelta(minutes=1)
    ingest(ag, [obs(lg)])
    run(ag)
    assert statuses(ag, lg) == ["DISMISSED"]
    ag.clk[0] = NOW + timedelta(days=7)
    ingest(ag, [obs(lg)])
    run(ag)
    assert statuses(ag, lg) == ["DISMISSED", "PENDING"]


def test_resolution_errors(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    rid = records(ag, lg)[0][6]
    with ag.maker() as db, pytest.raises(HTTPException) as e:
        svc.resolve_record(db, record_id=uuid.uuid4(), action="AUTHORIZE", admin_id=ag.admin_id, now=NOW)
    assert e.value.status_code == 404
    with ag.maker() as db, pytest.raises(ValueError):
        svc.resolve_record(db, record_id=rid, action="DETRUIRE", admin_id=ag.admin_id, now=NOW)
    with ag.maker() as db:
        svc.resolve_record(db, record_id=rid, action="DISMISS", admin_id=ag.admin_id, now=NOW)
    with ag.maker() as db, pytest.raises(HTTPException) as e:
        svc.resolve_record(db, record_id=rid, action="AUTHORIZE", admin_id=ag.admin_id, now=NOW)
    assert e.value.status_code == 409


def test_exemption_errors_and_uniqueness(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    with ag.maker() as db, pytest.raises(HTTPException) as e:
        svc.end_exemption(db, exemption_id=uuid.uuid4(), admin_id=ag.admin_id, now=NOW)
    assert e.value.status_code == 404
    with ag.maker() as db:
        svc.resolve_record(db, record_id=records(ag, lg)[0][6], action="AUTHORIZE", admin_id=ag.admin_id, now=NOW)
    iid = identity_id(ag, lg)
    with pytest.raises(IntegrityError):                         # une seule exemption active par identité
        with ag.engine.begin() as c:
            c.execute(text("INSERT INTO identity_exemptions (exit_node_identity_id) VALUES (:i)"), {"i": iid})
    [(eid,)] = rows(ag, "SELECT id FROM identity_exemptions WHERE exit_node_identity_id = :i", i=iid)
    with ag.maker() as db:
        svc.end_exemption(db, exemption_id=eid, admin_id=ag.admin_id, now=NOW)
    with ag.maker() as db, pytest.raises(HTTPException) as e:
        svc.end_exemption(db, exemption_id=eid, admin_id=ag.admin_id, now=NOW)
    assert e.value.status_code == 409
    with ag.engine.begin() as c:                                # après retrait, une nouvelle exemption est possible
        c.execute(text("INSERT INTO identity_exemptions (exit_node_identity_id) VALUES (:i)"), {"i": iid})


# ----------------------------------------------------------- traitement et verrou

def test_plan_mode_writes_nothing_and_reports_what_it_would_do(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    tables = ("quarantine_records", "enforcement_decisions", "identity_exemptions", "audit_logs", "job_runs")

    def snapshot():
        return [rows(ag, f"SELECT count(*) FROM {t}")[0][0] for t in tables]

    before = snapshot()
    report = svc.run_job(ag.maker, FixedClock(NOW), apply=False)
    assert snapshot() == before
    assert [(p.login_name, p.kind, p.to_status) for p in report.planned if p.login_name == lg] == [(lg, "OPEN", "PENDING")]


def test_a_job_run_is_recorded_without_any_personal_data_and_the_lock_is_exclusive(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    report = svc.run_job(ag.maker, FixedClock(NOW))
    assert report is not None and report.opened >= 1
    [(status, details, error)] = rows(ag, "SELECT status, details, error FROM job_runs WHERE job_name = 'reconcile' "
                                          "ORDER BY id DESC LIMIT 1")
    assert (status, error) == ("SUCCESS", None) and details["opened"] >= 1 and lg not in str(details)
    conn = ag.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": svc.LOCK_KEY})
    try:
        assert svc.run_job(ag.maker, FixedClock(NOW)) is None
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": svc.LOCK_KEY})
        conn.close()
    assert svc.run_job(ag.maker, FixedClock(NOW)) is not None


def test_a_failing_job_is_recorded_as_failed_and_the_error_is_raised(ag, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret-login@example.test")

    monkeypatch.setattr(svc, "reconcile", boom)
    with pytest.raises(RuntimeError):
        svc.run_job(ag.maker, FixedClock(NOW))
    [(status, error)] = rows(ag, "SELECT status, error FROM job_runs WHERE job_name = 'reconcile' ORDER BY id DESC LIMIT 1")
    assert (status, error) == ("FAILED", "RuntimeError")


def test_decisions_carry_no_login(ag):
    lg = blocked_identity(ag)
    context = rows(ag, "SELECT d.context FROM enforcement_decisions d JOIN exit_node_identities i "
                       "ON i.id = d.exit_node_identity_id WHERE i.login_name = :l", l=lg)
    assert context and lg not in str(context)
