"""Intégration lot 8c : réception du heartbeat de l'agent (PostgreSQL réel)."""
import json
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
PATH = "/api/agent/heartbeat"
NOW_DT = datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc)
NOW = NOW_DT.timestamp()
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
        yield SimpleNamespace(c=c, engine=engine, name=name, key_id=key_id, secret=secret)


def details(**over):
    base = {"lines": 113, "unparsed": 0, "timeouts_loaded": 33, "observations": 2, "conntrack_saturated": False,
            "dropped_batches": 0, "spool_recovered_corruption": False, "cycles": 4, "loop_errors": 0,
            "cycle_failed": False, "conntrack_saturation": 0.015, "spool_oldest_age_seconds": 180.6,
            "sent_batches": 4, "rejected_batches": 0, "send_failures": 0, "last_send_error": None,
            "clock_skew_seconds": 1, "backend_reachable": True}
    base.update(over)
    return base


def hb(**over):
    base = {"schema": 1, "agent_version": "0.1.0-dev", "generated_at": "2040-01-01T11:59:50Z",
            "conntrack_ok": True, "status_ok": True, "whois_ok": True, "bytes_available": False,
            "spool_depth": 3, "conntrack_count": 283, "conntrack_max": 7680, "last_error": None,
            "details": details()}
    base.update(over)
    return base


def post(ag, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    h = build_headers(ag.key_id, bytes.fromhex(ag.secret), "POST", PATH, body, now=NOW)
    return ag.c.post(PATH, content=body, headers=h)


def rows(ag):
    with ag.engine.connect() as c:
        return c.execute(text(
            "SELECT h.conntrack_ok, h.spool_depth, h.protocol_version, h.last_error, h.details "
            "FROM agent_heartbeats h JOIN exit_nodes n ON n.id = h.exit_node_id WHERE n.name = :n ORDER BY h.id"),
            {"n": ag.name}).all()


def test_heartbeat_requires_authentication(ag):
    assert ag.c.post(PATH, content=b"{}").status_code == 401


def test_a_heartbeat_is_stored_with_its_details(ag):
    r = post(ag, hb())
    assert r.status_code == 200 and r.json() == {"status": "ok", "server_time": int(NOW)}
    [(ok, depth, proto, err, det)] = rows(ag)
    assert (ok, depth, proto, err) == (True, 3, 1, None)
    assert det["cycles"] == 4 and det["clock_skew_seconds"] == 1 and det["generated_at"].startswith("2040-01-01T11:59:50")


def test_each_heartbeat_is_a_new_row_and_a_degraded_one_keeps_its_category(ag):
    assert post(ag, hb()).status_code == 200
    assert post(ag, hb(conntrack_ok=False, last_error="sudo_denied", conntrack_count=None)).status_code == 200
    stored = rows(ag)
    assert len(stored) == 2 and stored[1][0] is False and stored[1][3] == "sudo_denied"


def test_invalid_heartbeats_are_refused_without_echoing_values_and_store_nothing(ag):
    r = post(ag, hb(last_error="secret login@example.test"))
    assert r.status_code == 422 and "secret" not in r.text
    assert post(ag, {**hb(), "bonus": 1}).status_code == 422
    assert post(ag, hb(spool_depth="3")).status_code == 422
    assert post(ag, hb(details=details(login="a@b.test"))).status_code == 422
    assert post(ag, hb(schema=2)).status_code == 422
    assert post(ag, b"pas du json").status_code == 422
    assert rows(ag) == []
