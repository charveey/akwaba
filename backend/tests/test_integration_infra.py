"""Intégration lot 2c : infrastructure, observations, quarantaine, décisions."""
import os
import pathlib
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent


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


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def node(c):
    return c.execute(
        text("INSERT INTO exit_nodes (name, tailscale_ip) VALUES (:n, '100.85.214.5') RETURNING id"),
        {"n": f"n-{_uid()}"},
    ).scalar_one()


def ident(c, node_id, login=None):
    return c.execute(
        text("INSERT INTO exit_node_identities (exit_node_id, login_name, is_sharee) VALUES (:n, :l, true) RETURNING id"),
        {"n": node_id, "l": login or f"{_uid()}@example.test"},
    ).scalar_one()


def batch(c, node_id, batch_id=None):
    bid = batch_id or uuid.uuid4()
    c.execute(
        text(
            "INSERT INTO agent_batches (batch_id, exit_node_id, protocol_version, window_start, window_end, sample_count) "
            "VALUES (:b, :n, 1, now() - interval '1 minute', now(), 1)"
        ),
        {"b": bid, "n": node_id},
    )
    return bid


def obs(c, bid, node_id, identity_id, status):
    c.execute(
        text(
            "INSERT INTO exit_node_observations (batch_id, exit_node_id, identity_id, tailscale_ip, observed_at, "
            "window_seconds, resolution_status, active_flows, new_flows, replied_flows) "
            "VALUES (:b, :n, :i, '100.76.142.103', now(), 60, :s, 3, 1, 3)"
        ),
        {"b": bid, "n": node_id, "i": identity_id, "s": status},
    )


def session_row(c, node_id, identity_id, closed=False):
    closed_sql = "now()" if closed else "NULL"
    c.execute(
        text(
            "INSERT INTO exit_node_sessions (exit_node_id, identity_id, tailscale_ip, first_seen_at, last_seen_at, "
            f"closed_at, sample_count, new_flows_total, peak_active_flows) "
            f"VALUES (:n, :i, '100.76.142.103', now(), now(), {closed_sql}, 1, 1, 3)"
        ),
        {"n": node_id, "i": identity_id},
    )


def quarantine(c, identity_id, status="PENDING"):
    resolved = "now()" if status in ("AUTHORIZED", "DISMISSED") else "NULL"
    return c.execute(
        text(
            "INSERT INTO quarantine_records (exit_node_identity_id, status, started_at, expires_at, resolved_at) "
            f"VALUES (:i, :s, now(), now() + interval '24 hours', {resolved}) RETURNING id"
        ),
        {"i": identity_id, "s": status},
    ).scalar_one()


def decision(c, identity_id, decision="WOULD_BLOCK", mode="dry_run", enforced=False):
    c.execute(
        text(
            "INSERT INTO enforcement_decisions (exit_node_identity_id, decision, mode, enforced, reason) "
            "VALUES (:i, :d, :m, :e, 'test')"
        ),
        {"i": identity_id, "d": decision, "m": mode, "e": enforced},
    )


def test_infra_tables_exist(engine):
    with engine.connect() as c:
        names = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
    assert {
        "exit_nodes", "agent_credentials", "agent_heartbeats", "agent_batches", "exit_node_identities",
        "exit_node_peers", "exit_node_observations", "exit_node_sessions", "tailnet_members",
        "tailnet_devices", "protected_identities", "quarantine_records", "enforcement_decisions",
    } <= names


def test_infra_models_and_migrations_are_in_sync(engine):
    alembic("check")


def test_unresolved_observation_has_no_identity(engine):
    with engine.begin() as c:
        n = node(c)
        obs(c, batch(c, n), n, None, "UNRESOLVED")
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            n = node(c)
            obs(c, batch(c, n), n, ident(c, n), "UNRESOLVED")


def test_resolved_observation_requires_identity(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            n = node(c)
            obs(c, batch(c, n), n, None, "RESOLVED")


def test_no_destination_columns_are_stored(engine):
    with engine.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name IN ('exit_node_observations', 'exit_node_sessions', 'exit_node_peers')"))}
    assert not {x for x in cols if "dst" in x or "dest" in x or "port" in x}


def test_batch_id_is_the_idempotency_key(engine):
    bid = uuid.uuid4()
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            n = node(c)
            batch(c, n, bid)
            batch(c, n, bid)


def test_identity_login_must_be_lowercase(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            ident(c, node(c), "Upper@Example.test")


def test_identity_is_unique_per_exit_node(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            n = node(c)
            ident(c, n, "same@example.test")
            ident(c, n, "same@example.test")


def test_only_one_open_session_per_identity_and_ip(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            n = node(c)
            i = ident(c, n)
            session_row(c, n, i)
            session_row(c, n, i)
    with engine.begin() as c:
        n = node(c)
        i = ident(c, n)
        session_row(c, n, i, closed=True)
        session_row(c, n, i)


def test_only_one_open_quarantine_per_identity(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            i = ident(c, node(c))
            quarantine(c, i)
            quarantine(c, i, "EXPIRED")


def test_closed_quarantine_allows_a_new_one(engine):
    with engine.begin() as c:
        i = ident(c, node(c))
        quarantine(c, i, "DISMISSED")
        quarantine(c, i)


def test_quarantine_resolution_is_consistent(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO quarantine_records (exit_node_identity_id, status, started_at, expires_at) "
                    "VALUES (:i, 'AUTHORIZED', now(), now() + interval '24 hours')"
                ),
                {"i": ident(c, node(c))},
            )


def test_decisions_must_be_dry_run_and_never_enforced(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            decision(c, ident(c, node(c)), enforced=True)
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            decision(c, ident(c, node(c)), mode="enforce")
    with engine.begin() as c:
        decision(c, ident(c, node(c)))


def test_decisions_are_append_only(engine):
    with engine.begin() as c:
        decision(c, ident(c, node(c)), "WOULD_UNBLOCK")
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as c:
            c.execute(text("UPDATE enforcement_decisions SET reason = 'x'"))
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM enforcement_decisions"))
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as c:
            c.execute(text("TRUNCATE enforcement_decisions"))


def test_protected_identity_is_unique_per_active_source(engine):
    login = f"{_uid()}@example.test"
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            for _ in range(2):
                c.execute(text("INSERT INTO protected_identities (login_name, source) VALUES (:l, 'AUTO_MEMBER')"), {"l": login})
    with engine.begin() as c:
        for source in ("AUTO_MEMBER", "MANUAL"):
            c.execute(text("INSERT INTO protected_identities (login_name, source) VALUES (:l, :s)"),
                      {"l": f"{_uid()}@example.test", "s": source})


def test_agent_key_id_is_unique(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            n = node(c)
            for _ in range(2):
                c.execute(text("INSERT INTO agent_credentials (exit_node_id, key_id, secret_encrypted) VALUES (:n, 'k1', 'x')"), {"n": n})
