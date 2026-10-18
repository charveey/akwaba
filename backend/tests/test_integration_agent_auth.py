"""Intégration lot 8a : authentification HMAC de l'agent (PostgreSQL réel, horloge contrôlée)."""
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
from app.core.agent_protocol import MAX_BODY_BYTES, NonceCache, build_headers
from app.core.crypto import decrypt_field
from app.db.session import get_db
from app.domain.clock import FixedClock
from app.main import create_app
from app.services import agent_admin

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent
PATH = "/api/agent/ping"
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

    peer = [IP]
    cache = NonceCache()
    app = create_app()
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_clock] = lambda: FixedClock(NOW_DT)
    app.dependency_overrides[get_nonce_cache] = lambda: cache
    app.dependency_overrides[get_peer_ip] = lambda: peer[0]
    app.dependency_overrides[get_agent_networks] = lambda: [IP]
    name = f"n-{uuid.uuid4().hex[:8]}"
    with maker() as db:
        key_id, secret = agent_admin.create_agent(db, name=name, ip=IP, rotate=False, now=NOW_DT)
    with TestClient(app, base_url="https://testserver") as c:
        yield SimpleNamespace(c=c, maker=maker, engine=engine, name=name, key_id=key_id, secret=secret, peer=peer)


def call(ag, body=b"{}", *, secret=None, key_id=None, now=None, nonce=None, path=PATH, url=None):
    h = build_headers(key_id or ag.key_id, bytes.fromhex(secret or ag.secret), "POST", path, body,
                      now=NOW if now is None else now, nonce=nonce)
    return ag.c.post(url or path, content=body, headers=h)


def test_a_valid_request_is_accepted_and_returns_the_server_time(ag):
    r = call(ag, b'{"hello":"world"}')
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "exit_node": ag.name, "server_time": int(NOW)}


def test_last_used_at_is_recorded(ag):
    call(ag)
    with ag.engine.connect() as c:
        used = c.execute(text("SELECT last_used_at FROM agent_credentials WHERE key_id = :k"),
                         {"k": ag.key_id}).scalar_one()
    assert used is not None


def test_a_tampered_body_or_a_wrong_secret_is_refused(ag):
    h = build_headers(ag.key_id, bytes.fromhex(ag.secret), "POST", PATH, b'{"a":1}', now=NOW)
    assert ag.c.post(PATH, content=b'{"a":2}', headers=h).status_code == 401
    assert call(ag, secret="22" * 32).status_code == 401


def test_replay_of_the_same_nonce_is_refused(ag):
    assert call(ag, nonce="ab" * 16).status_code == 200
    assert call(ag, nonce="ab" * 16).status_code == 401
    assert call(ag, nonce="cd" * 16).status_code == 200


def test_an_invalid_signature_does_not_burn_the_nonce(ag):
    assert call(ag, secret="22" * 32, nonce="ef" * 16).status_code == 401
    assert call(ag, nonce="ef" * 16).status_code == 200


def test_clock_skew_is_refused_but_the_window_edge_is_accepted(ag):
    assert call(ag, now=NOW + 500).status_code == 401
    assert call(ag, now=NOW - 500).status_code == 401
    assert call(ag, now=NOW + 120).status_code == 200


def test_unknown_agent_and_missing_headers_are_refused(ag):
    assert call(ag, key_id="agt_inconnu").status_code == 401
    assert ag.c.post(PATH, content=b"{}").status_code == 401


def test_a_query_string_is_refused(ag):
    assert call(ag, url=PATH + "?x=1").status_code == 401


def test_a_signature_for_another_path_is_refused(ag):
    assert call(ag, path="/api/agent/other", url=PATH).status_code == 401


def test_ip_outside_the_allowlist_is_refused(ag):
    ag.peer[0] = "10.1.2.3"
    assert call(ag).status_code == 401
    ag.peer[0] = None
    assert call(ag).status_code == 401


def test_oversized_body_is_refused_before_authentication(ag):
    assert ag.c.post(PATH, content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413


def test_revoked_credential_is_refused(ag):
    with ag.maker() as db:
        agent_admin.revoke_agent(db, ag.key_id, now=NOW_DT)
    assert call(ag).status_code == 401
    with ag.maker() as db, pytest.raises(agent_admin.AgentAdminError):
        agent_admin.revoke_agent(db, ag.key_id, now=NOW_DT)


def test_inactive_exit_node_is_refused(ag):
    with ag.engine.begin() as c:
        c.execute(text("UPDATE exit_nodes SET is_active = false WHERE name = :n"), {"n": ag.name})
    assert call(ag).status_code == 401


def test_all_refusals_have_the_same_status_and_message(ag):
    answers = [
        call(ag, secret="22" * 32), call(ag, key_id="agt_inconnu"), call(ag, now=NOW + 500),
        ag.c.post(PATH, content=b"{}"), call(ag, url=PATH + "?x=1"),
    ]
    assert {r.status_code for r in answers} == {401}
    assert len({r.text for r in answers}) == 1


def test_secret_is_stored_encrypted_and_never_in_the_audit(ag):
    with ag.engine.connect() as c:
        enc = c.execute(text("SELECT secret_encrypted FROM agent_credentials WHERE key_id = :k"),
                        {"k": ag.key_id}).scalar_one()
        audit = c.execute(text("SELECT action, details FROM audit_logs WHERE entity_id = :k"),
                          {"k": ag.key_id}).all()
    assert enc != ag.secret and ag.secret not in enc and decrypt_field(enc) == ag.secret
    assert [a for a, _ in audit] == ["agent.credential_created"]
    assert ag.secret not in str(audit)


def test_rotation_revokes_the_previous_credential(ag):
    with ag.maker() as db:
        with pytest.raises(agent_admin.AgentAdminError):
            agent_admin.create_agent(db, name=ag.name, ip=IP, rotate=False, now=NOW_DT)
        with pytest.raises(agent_admin.AgentAdminError):
            agent_admin.create_agent(db, name=ag.name, ip="100.1.1.1", rotate=True, now=NOW_DT)
        new_key, new_secret = agent_admin.create_agent(db, name=ag.name, ip=IP, rotate=True, now=NOW_DT)
    assert call(ag).status_code == 401                                   # ancien identifiant révoqué
    assert call(ag, key_id=new_key, secret=new_secret).status_code == 200


def test_invalid_agent_creation_inputs_are_refused(ag):
    with ag.maker() as db:
        for name, ip in (("", IP), ("x", "pas-une-ip")):
            with pytest.raises(agent_admin.AgentAdminError):
                agent_admin.create_agent(db, name=name, ip=ip, rotate=False, now=NOW_DT)
        with pytest.raises(agent_admin.AgentAdminError):
            agent_admin.revoke_agent(db, "agt_inexistant", now=NOW_DT)
