"""Intégration lot 8b : ingestion des lots (PostgreSQL réel, horloge contrôlée)."""
import json
import os
import pathlib
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.agent_deps import get_agent_networks, get_nonce_cache, get_peer_ip
from app.api.deps import get_clock
from app.core.agent_protocol import NonceCache, build_headers
from app.db.session import get_db
from app.domain.clock import FixedClock
from app.main import create_app
from app.services import agent_admin

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent
PATH = "/api/agent/batches"
NOW_DT = datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc)
NOW = NOW_DT.timestamp()
BASE = NOW_DT - timedelta(hours=1)
IP = "100.85.214.5"


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

    cache = NonceCache()
    app = create_app()
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_clock] = lambda: FixedClock(NOW_DT)
    app.dependency_overrides[get_nonce_cache] = lambda: cache
    app.dependency_overrides[get_peer_ip] = lambda: IP
    app.dependency_overrides[get_agent_networks] = lambda: [IP]
    name = f"n-{uuid.uuid4().hex[:8]}"
    with maker() as db:
        key_id, secret = agent_admin.create_agent(db, name=name, ip=IP, rotate=False, now=NOW_DT)
    with TestClient(app, base_url="https://testserver") as c:
        yield SimpleNamespace(c=c, maker=maker, engine=engine, name=name, key_id=key_id, secret=secret)


def obs(ip="100.76.142.103", login="user@example.test", status="RESOLVED", stable="nSTABLE1", **over):
    base = {
        "src_ip": ip, "total_flows": 10, "recent_flows": 8, "recent_replied_flows": 6, "unknown_age_flows": 1,
        "new_flows": 2, "active_window": True, "consecutive_active_windows": 2, "active_confirmed": True,
        "identity_status": status, "identity_technical_error": status == "UNRESOLVED",
        "identity": None if status == "UNRESOLVED" else
        {"login_name": login, "tailscale_user_id": 7, "stable_id": stable, "is_sharee": True},
        "peer": None,
    }
    base.update(over)
    return base


def batch(observations, at=BASE, bid=None, **over):
    base = {"batch_id": str(bid or uuid.uuid4()), "schema": 1, "sampled_at": at.isoformat().replace("+00:00", "Z"),
            "window_seconds": 60, "conntrack_ok": True, "status_ok": True, "whois_ok": True,
            "observations": observations}
    base.update(over)
    return base


def post(ag, payload, *, key_id=None, secret=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    h = build_headers(key_id or ag.key_id, bytes.fromhex(secret or ag.secret), "POST", PATH, body, now=NOW)
    return ag.c.post(PATH, content=body, headers=h)


def q(ag, sql, **params):
    with ag.engine.connect() as c:
        return c.execute(text(sql), params).all()


def node_id(ag):
    return q(ag, "SELECT id FROM exit_nodes WHERE name = :n", n=ag.name)[0][0]


def sessions(ag):
    return q(ag, "SELECT sample_count, closed_at, first_seen_at, last_seen_at, new_flows_total, peak_active_flows "
                 "FROM exit_node_sessions WHERE exit_node_id = :n ORDER BY first_seen_at", n=node_id(ag))


def test_batch_requires_authentication(ag):
    assert ag.c.post(PATH, content=b"{}").status_code == 401


def test_a_batch_is_stored_with_identity_peer_and_observation(ag):
    bid = uuid.uuid4()
    r = post(ag, batch([obs()], bid=bid))
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "accepted", "batch_id": str(bid), "observations": 1}
    row = q(ag, "SELECT sample_count, protocol_version FROM agent_batches WHERE batch_id = :b", b=str(bid))
    assert row == [(1, 1)]
    o = q(ag, "SELECT active_flows, new_flows, replied_flows, total_flows, unknown_age_flows, "
              "consecutive_active_windows, active_confirmed, resolution_status, bytes_in, tailscale_ip, "
              "identity_id IS NOT NULL, peer_id IS NOT NULL FROM exit_node_observations WHERE batch_id = :b",
          b=str(bid))
    assert o == [(8, 2, 6, 10, 1, 2, True, "RESOLVED", None, "100.76.142.103", True, True)]
    ident = q(ag, "SELECT login_name, tailscale_user_id, is_sharee, client_identity_id FROM exit_node_identities "
                  "WHERE exit_node_id = :n", n=node_id(ag))
    assert ident == [("user@example.test", 7, True, None)]
    peer = q(ag, "SELECT stable_id, tailscale_ip FROM exit_node_peers WHERE exit_node_id = :n", n=node_id(ag))
    assert peer == [("nSTABLE1", "100.76.142.103")]


def test_a_replayed_batch_is_acknowledged_but_not_stored_twice(ag):
    bid = uuid.uuid4()
    assert post(ag, batch([obs()], bid=bid)).json()["status"] == "accepted"
    again = post(ag, batch([obs()], bid=bid))
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    assert q(ag, "SELECT count(*) FROM exit_node_observations WHERE batch_id = :b", b=str(bid)) == [(1,)]
    assert q(ag, "SELECT count(*) FROM agent_batches WHERE batch_id = :b", b=str(bid)) == [(1,)]


def test_the_same_batch_id_from_another_exit_node_is_a_conflict(ag):
    bid = uuid.uuid4()
    assert post(ag, batch([obs()], bid=bid)).status_code == 200
    with ag.maker() as db:
        key2, secret2 = agent_admin.create_agent(db, name=ag.name + "-b", ip=IP, rotate=False, now=NOW_DT)
    assert post(ag, batch([obs()], bid=bid), key_id=key2, secret=secret2).status_code == 409


def test_unknown_new_flows_are_stored_as_null_not_zero(ag):
    bid = uuid.uuid4()
    assert post(ag, batch([obs(new_flows=None)], bid=bid)).status_code == 200
    assert q(ag, "SELECT new_flows FROM exit_node_observations WHERE batch_id = :b", b=str(bid)) == [(None,)]


def test_an_unresolved_observation_creates_no_identity_peer_or_session(ag):
    bid = uuid.uuid4()
    assert post(ag, batch([obs(status="UNRESOLVED")], bid=bid)).status_code == 200
    row = q(ag, "SELECT resolution_status, identity_id, peer_id, identity_technical_error "
                "FROM exit_node_observations WHERE batch_id = :b", b=str(bid))
    assert row == [("UNRESOLVED", None, None, True)]
    assert q(ag, "SELECT count(*) FROM exit_node_identities WHERE exit_node_id = :n", n=node_id(ag)) == [(0,)]
    assert q(ag, "SELECT count(*) FROM exit_node_peers WHERE exit_node_id = :n", n=node_id(ag)) == [(0,)]
    assert sessions(ag) == []


def test_identities_and_peers_are_upserted_not_duplicated(ag):
    post(ag, batch([obs()], at=BASE))
    post(ag, batch([obs(ip="100.76.142.200")], at=BASE + timedelta(minutes=1)))
    ident = q(ag, "SELECT count(*), min(first_seen_at), max(last_seen_at) FROM exit_node_identities "
                  "WHERE exit_node_id = :n", n=node_id(ag))[0]
    assert ident[0] == 1 and ident[1] == BASE and ident[2] == BASE + timedelta(minutes=1)
    assert q(ag, "SELECT tailscale_ip FROM exit_node_peers WHERE exit_node_id = :n", n=node_id(ag)) == [("100.76.142.200",)]


def test_consecutive_observations_extend_one_session(ag):
    post(ag, batch([obs(new_flows=2, recent_flows=8)], at=BASE))
    post(ag, batch([obs(new_flows=None, recent_flows=9, total_flows=12)], at=BASE + timedelta(minutes=1)))
    s = sessions(ag)
    assert len(s) == 1
    assert (s[0][0], s[0][1], s[0][2], s[0][3], s[0][4], s[0][5]) == (
        2, None, BASE, BASE + timedelta(minutes=1), 2, 9)


def test_a_long_gap_closes_the_session_and_opens_a_new_one(ag):
    post(ag, batch([obs()], at=BASE))
    post(ag, batch([obs()], at=BASE + timedelta(minutes=11)))
    s = sessions(ag)
    assert len(s) == 2
    assert s[0][1] == BASE and s[1][1] is None and s[1][0] == 1


def test_stale_sessions_of_other_identities_are_closed_by_a_later_batch(ag):
    post(ag, batch([obs(login="a@example.test", stable="nA")], at=BASE))
    post(ag, batch([obs(ip="100.91.19.91", login="b@example.test", stable="nB")], at=BASE + timedelta(minutes=20)))
    s = sessions(ag)
    assert len(s) == 2 and s[0][1] == BASE and s[1][1] is None


def test_an_old_batch_arriving_late_keeps_its_observation_but_not_a_new_session(ag):
    post(ag, batch([obs()], at=BASE + timedelta(minutes=30)))
    assert post(ag, batch([obs()], at=BASE)).status_code == 200
    assert q(ag, "SELECT count(*) FROM exit_node_observations WHERE exit_node_id = :n", n=node_id(ag)) == [(2,)]
    assert len(sessions(ag)) == 1


def test_invalid_batches_are_refused_without_echoing_values_and_store_nothing(ag):
    bad = batch([obs(login="secret login@example.test")])
    r = post(ag, bad)
    assert r.status_code == 422 and "secret" not in r.text
    assert q(ag, "SELECT count(*) FROM agent_batches WHERE batch_id = :b", b=bad["batch_id"]) == [(0,)]
    assert post(ag, b"pas du json").status_code == 422
    assert post(ag, batch([obs()], schema=2)).status_code == 422
    assert post(ag, batch([obs(ip="203.0.113.10")])).status_code == 422
    assert post(ag, batch([obs(), obs()])).status_code == 422
    assert post(ag, batch([obs()], conntrack_ok=False)).status_code == 422


def test_future_and_ancient_batches_are_refused(ag):
    assert post(ag, batch([obs()], at=NOW_DT + timedelta(seconds=121))).status_code == 422
    assert post(ag, batch([obs()], at=NOW_DT + timedelta(seconds=60))).status_code == 200
    assert post(ag, batch([obs()], at=NOW_DT - timedelta(days=31))).status_code == 422


def test_an_empty_batch_is_accepted(ag):
    bid = uuid.uuid4()
    r = post(ag, batch([], bid=bid))
    assert r.status_code == 200 and r.json()["observations"] == 0
