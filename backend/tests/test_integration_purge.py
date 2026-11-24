"""Intégration lot 10e : purge (PostgreSQL réel, horloge contrôlée, données semées par SQL)."""
import os
import pathlib
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.domain.clock import FixedClock
from app.models.infra import EnforcementDecision, QuarantineRecord
from app.services import agent_admin
from app.services import purge as svc

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


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
    name = f"n-{uuid.uuid4().hex[:8]}"
    with maker() as db:
        agent_admin.create_agent(db, name=name, ip="100.85.214.5", rotate=False, now=NOW)
    with engine.connect() as c:
        node_id = c.execute(text("SELECT id FROM exit_nodes WHERE name = :n"), {"n": name}).scalar_one()
    return SimpleNamespace(maker=maker, engine=engine, node_id=node_id, name=name, marker=f"t-{uuid.uuid4().hex[:8]}")


def run(ag, **kw):
    with ag.maker() as db:
        return svc.purge(db, FixedClock(NOW), **kw)


def one(ag, sql, **p):
    with ag.engine.connect() as c:
        return c.execute(text(sql), p).scalar_one()


def seed_heartbeat(ag, at):
    with ag.engine.begin() as c:
        c.execute(text("INSERT INTO agent_heartbeats (exit_node_id, received_at, agent_version, protocol_version, "
                       "conntrack_ok, status_ok, whois_ok, bytes_available, spool_depth) "
                       "VALUES (:n, :t, 'test', 1, true, true, true, false, 0)"), {"n": ag.node_id, "t": at})


def seed_job(ag, at, status="SUCCESS"):
    with ag.engine.begin() as c:
        c.execute(text("INSERT INTO job_runs (job_name, started_at, status) VALUES (:j, :t, :s)"),
                  {"j": ag.marker, "t": at, "s": status})


def seed_batch(ag, received_at, observed_at=None, observations=1):
    bid = uuid.uuid4()
    with ag.engine.begin() as c:
        c.execute(text("INSERT INTO agent_batches (batch_id, exit_node_id, protocol_version, received_at, "
                       "window_start, window_end, sample_count) VALUES (:b, :n, 1, :r, :s, :r, :c)"),
                  {"b": bid, "n": ag.node_id, "r": received_at, "s": received_at - timedelta(seconds=60),
                   "c": observations})
        for _ in range(observations):
            c.execute(text("INSERT INTO exit_node_observations (batch_id, exit_node_id, tailscale_ip, observed_at, "
                           "window_seconds, resolution_status, active_flows, new_flows, replied_flows) "
                           "VALUES (:b, :n, '100.64.0.1', :o, 60, 'UNRESOLVED', 1, 1, 1)"),
                      {"b": bid, "n": ag.node_id, "o": observed_at or received_at})
    return bid


def seed_identity(ag):
    with ag.engine.begin() as c:
        return c.execute(text("INSERT INTO exit_node_identities (exit_node_id, login_name) VALUES (:n, :l) "
                              "RETURNING id"), {"n": ag.node_id, "l": f"u-{uuid.uuid4().hex[:8]}@example.test"}).scalar_one()


def seed_session(ag, identity, last_seen, closed=True, ip="100.64.0.9"):
    with ag.engine.begin() as c:
        c.execute(text("INSERT INTO exit_node_sessions (exit_node_id, identity_id, tailscale_ip, first_seen_at, "
                       "last_seen_at, closed_at, sample_count, new_flows_total, peak_active_flows) "
                       "VALUES (:n, :i, :ip, :f, :l, :c, 1, 0, 0)"),
                  {"n": ag.node_id, "i": identity, "ip": ip, "f": last_seen - timedelta(minutes=5), "l": last_seen,
                   "c": last_seen if closed else None})


def counts(ag):
    return {
        "heartbeats": one(ag, "SELECT count(*) FROM agent_heartbeats WHERE exit_node_id = :n", n=ag.node_id),
        "jobs": one(ag, "SELECT count(*) FROM job_runs WHERE job_name = :j", j=ag.marker),
        "observations": one(ag, "SELECT count(*) FROM exit_node_observations WHERE exit_node_id = :n", n=ag.node_id),
        "batches": one(ag, "SELECT count(*) FROM agent_batches WHERE exit_node_id = :n", n=ag.node_id),
        "sessions": one(ag, "SELECT count(*) FROM exit_node_sessions WHERE exit_node_id = :n", n=ag.node_id),
    }


def test_old_rows_are_deleted_and_recent_ones_are_kept_in_every_table(ag):
    ident = seed_identity(ag)
    for at in (NOW - 31 * DAY, NOW - 29 * DAY):
        seed_heartbeat(ag, at)
        seed_job(ag, at)
    for at in (NOW - 91 * DAY, NOW - 89 * DAY):
        seed_batch(ag, at)
    seed_session(ag, ident, NOW - 366 * DAY, ip="100.64.0.10")
    seed_session(ag, ident, NOW - 364 * DAY, ip="100.64.0.11")
    assert counts(ag) == {"heartbeats": 2, "jobs": 2, "observations": 2, "batches": 2, "sessions": 2}
    report = run(ag)
    assert counts(ag) == {"heartbeats": 1, "jobs": 1, "observations": 1, "batches": 1, "sessions": 1}
    assert report.errors == 0 and not report.capped and report.total >= 5
    assert one(ag, "SELECT min(received_at) FROM agent_heartbeats WHERE exit_node_id = :n", n=ag.node_id) == NOW - 29 * DAY
    run(ag)
    assert sum(counts(ag).values()) == 5                      # idempotent


def test_the_cutoff_is_exclusive(ag):
    seed_heartbeat(ag, NOW - 30 * DAY)                         # exactement à la limite : conservé
    seed_heartbeat(ag, NOW - 30 * DAY - timedelta(seconds=1))
    run(ag)
    assert counts(ag)["heartbeats"] == 1


def test_a_batch_is_deleted_only_when_none_of_its_observations_remain(ag):
    both_old = seed_batch(ag, NOW - 100 * DAY)
    catch_up = seed_batch(ag, NOW - 100 * DAY, observed_at=NOW - 10 * DAY)    # ancien lot, observation récente
    empty = seed_batch(ag, NOW - 100 * DAY, observations=0)
    recent = seed_batch(ag, NOW - 5 * DAY)
    report = run(ag)                                           # l'ordre observations -> lots ne lève aucune erreur
    assert report.errors == 0
    kept = {r[0] for r in ag.engine.connect().execute(
        text("SELECT batch_id FROM agent_batches WHERE exit_node_id = :n"), {"n": ag.node_id})}
    assert kept == {catch_up, recent} and both_old not in kept and empty not in kept
    assert counts(ag)["observations"] == 2


def test_open_sessions_are_never_purged(ag):
    ident = seed_identity(ag)
    seed_session(ag, ident, NOW - 400 * DAY, closed=False, ip="100.64.0.20")
    seed_session(ag, ident, NOW - 400 * DAY, closed=True, ip="100.64.0.21")
    run(ag)
    assert counts(ag)["sessions"] == 1
    assert one(ag, "SELECT count(*) FROM exit_node_sessions WHERE exit_node_id = :n AND closed_at IS NULL",
               n=ag.node_id) == 1


def test_a_capped_pass_leaves_the_rest_for_the_next_one(ag):
    for _ in range(7):
        seed_heartbeat(ag, NOW - 40 * DAY)
    first = run(ag, batch_size=3, max_chunks=2)
    assert counts(ag)["heartbeats"] == 1 and first.capped is True
    second = run(ag, batch_size=3, max_chunks=2)
    assert counts(ag)["heartbeats"] == 0 and second.capped is False


def test_plan_mode_counts_without_deleting(ag):
    for _ in range(2):
        seed_heartbeat(ag, NOW - 40 * DAY)
        seed_job(ag, NOW - 40 * DAY)
    seed_batch(ag, NOW - 100 * DAY)
    before = counts(ag)
    report = run(ag, apply=False)
    assert counts(ag) == before
    assert report.deleted["agent_heartbeats"] >= 2 and report.deleted["job_runs"] >= 2
    assert report.deleted["exit_node_observations"] >= 1 and report.deleted["agent_batches"] >= 1


def test_the_business_history_is_never_touched(ag):
    ident = seed_identity(ag)
    with ag.maker() as db:
        rec = QuarantineRecord(exit_node_identity_id=ident, status="PENDING",
                               started_at=NOW - 500 * DAY, expires_at=NOW - 499 * DAY)
        db.add(rec)
        db.flush()
        db.add(EnforcementDecision(exit_node_identity_id=ident, quarantine_record_id=rec.id, decision="WOULD_BLOCK",
                                   mode="dry_run", enforced=False, decided_at=NOW - 500 * DAY, reason="x", context={}))
        db.commit()
    tables = ("quarantine_records", "enforcement_decisions", "exit_node_identities", "exit_node_peers",
              "identity_exemptions", "audit_logs", "protected_identities", "members", "payments", "subscriptions")
    before = [one(ag, f"SELECT count(*) FROM {t}") for t in tables]
    seed_heartbeat(ag, NOW - 400 * DAY)
    run(ag)
    assert [one(ag, f"SELECT count(*) FROM {t}") for t in tables] == before
    assert one(ag, "SELECT count(*) FROM quarantine_records WHERE exit_node_identity_id = :i", i=ident) == 1
    assert one(ag, "SELECT count(*) FROM enforcement_decisions WHERE exit_node_identity_id = :i", i=ident) == 1


def test_invalid_arguments_are_refused(ag):
    for kw in ({"batch_size": 0}, {"max_chunks": 0}):
        with pytest.raises(ValueError):
            run(ag, **kw)


def test_a_purge_job_is_recorded_and_the_lock_is_exclusive(ag):
    seed_heartbeat(ag, NOW - 40 * DAY)
    report = svc.run_purge_job(ag.maker, FixedClock(NOW))
    assert report is not None and report.deleted["agent_heartbeats"] >= 1
    [(status, details, error)] = ag.engine.connect().execute(text(
        "SELECT status, details, error FROM job_runs WHERE job_name = 'purge' ORDER BY id DESC LIMIT 1")).all()
    assert (status, error) == ("SUCCESS", None) and details["deleted"]["agent_heartbeats"] >= 1
    conn = ag.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": svc.LOCK_KEY})
    try:
        assert svc.run_purge_job(ag.maker, FixedClock(NOW)) is None
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": svc.LOCK_KEY})
        conn.close()
    assert svc.run_purge_job(ag.maker, FixedClock(NOW)) is not None


def test_a_plan_run_writes_no_job_run(ag):
    before = one(ag, "SELECT count(*) FROM job_runs WHERE job_name = 'purge'")
    assert svc.run_purge_job(ag.maker, FixedClock(NOW), apply=False) is not None
    assert one(ag, "SELECT count(*) FROM job_runs WHERE job_name = 'purge'") == before


def test_a_failing_purge_is_recorded_as_failed_and_the_error_is_raised(ag, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret-login@example.test")

    monkeypatch.setattr(svc, "purge", boom)
    with pytest.raises(RuntimeError):
        svc.run_purge_job(ag.maker, FixedClock(NOW))
    [(status, error)] = ag.engine.connect().execute(text(
        "SELECT status, error FROM job_runs WHERE job_name = 'purge' ORDER BY id DESC LIMIT 1")).all()
    assert (status, error) == ("FAILED", "RuntimeError")
