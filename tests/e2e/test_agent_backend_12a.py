"""Phase 12 — lot 12a : l'agent RÉEL parle au backend RÉEL.

    agent (processus `python -m akwaba_agent`, code inchangé)
        ├─ `conntrack -L -f ipv4|ipv6`  → exécutable factice (fixture synthétique)
        ├─ `tailscale status --json`    → exécutable factice
        ├─ LocalAPI whois (socket Unix) → serveur factice du test
        └─ HMAC + HTTP direct ──► backend uvicorn :8000 (en production : IP tailnet:8000, sans Nginx)
                                     └─► PostgreSQL migré (Alembic)

Aucun appel tailscale, conntrack, nftables ni iptables réel. L'identifiant agent est créé et révoqué
avec la commande d'administration de production (`python -m app.cli create-agent / revoke-agent`).
Outils communs : agent_harness.py.
"""
import json
import re
import time

import pytest
from sqlalchemy import text

from agent_harness import Admin, Device, FakeExitNode, cli, create_agent, run_agent, signed_post, spawn_agent, summary
from conftest import ROOT, SKIP, Backend, free_port

pytestmark = SKIP

NODE_NAME = "exit-e2e-12a"
NODE_IP = "100.85.214.5"
CLIENT_A_IP, CLIENT_A_LOGIN = "100.76.142.103", "client-a@example.test"
CLIENT_A_V6 = "fd7a:115c:a1e0::1234:5678"
UNKNOWN_IP = "100.91.19.91"            # absent de la LocalAPI : whois 404 → UNRESOLVED
EXTRA_LINES = [                        # un second appareil, inconnu de tailscaled
    f"tcp      6 431996 ESTABLISHED src={UNKNOWN_IP} dst=203.0.113.20 sport=41000 dport=443 "
    f"src=203.0.113.20 dst=192.168.1.99 sport=443 dport=41000 [ASSURED] mark=0 use=1",
]


# --------------------------------------------------------------------------- environnement factice

@pytest.fixture(scope="module")
def fake_host():
    """Faux Exit Node : fixture conntrack synthétique du dépôt + un appareil inconnu de tailscaled."""
    node = FakeExitNode()
    client_a = dict(login=CLIENT_A_LOGIN.upper(), user_id=4242, stable_id="nClientA1", hostname="telephone-a")
    node.configure([Device(CLIENT_A_IP, **client_a), Device(CLIENT_A_V6, **client_a)],
                   extra_lines=(ROOT / "tests" / "fixtures" / "conntrack_synthetic.txt").read_text().splitlines()
                   + EXTRA_LINES)
    yield node
    node.close()


_cli = cli


@pytest.fixture(scope="module")
def agent(stack, fake_host):
    return create_agent(stack, fake_host, NODE_NAME, NODE_IP)


def scalar(stack, sql, **params):
    with stack["engine"].connect() as c:
        return c.execute(text(sql), params).scalar()


def node_id(stack):
    return scalar(stack, "SELECT id FROM exit_nodes WHERE name = :n", n=NODE_NAME)


def backend_log(stack) -> str:
    return (stack["work"] / "backend.log").read_text(errors="replace")


# --------------------------------------------------------------------------- scénarios

def test_signed_ping_goes_straight_to_the_backend(fake_host, agent):
    res = run_agent(fake_host, agent, "--ping")
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"ping=ok noeud={NODE_NAME}" in res.stdout
    # Même machine : l'écart ne vient que de l'en-tête HTTP Date (à la seconde, mis en cache par uvicorn).
    skew = int(re.search(r"ecart_horloge=(-?\d+)s", res.stdout).group(1))
    assert abs(skew) <= 3, res.stdout


def test_real_cycles_reach_the_database_and_the_admin_view(stack, fake_host, agent):
    spool = fake_host.base / "spool" / "spool.db"
    spool.parent.mkdir()
    res = run_agent(fake_host, agent, "--cycles", "3", "--interval", "1", "--window", "5", "--quiet",
                    "--spool", str(spool))
    assert res.returncode == 0, res.stdout + res.stderr
    s = summary(res.stdout)
    assert (s["cycles"], s["lots_ecrits"], s["envoyes"], s["refuses"], s["erreurs"]) == ("3", "3", "3", "0", "0"), s

    from akwaba_agent.spool.store import Spool
    assert Spool(str(spool)).pending() == 0, "le spool doit être vidé après envoi"

    nid = node_id(stack)
    assert scalar(stack, "SELECT count(*) FROM agent_batches WHERE exit_node_id = :n", n=nid) == 3
    assert scalar(stack, "SELECT count(*) FROM agent_heartbeats WHERE exit_node_id = :n "
                         "AND conntrack_ok AND status_ok AND whois_ok", n=nid) == 3
    assert scalar(stack, "SELECT conntrack_count FROM agent_heartbeats WHERE exit_node_id = :n "
                         "ORDER BY id DESC LIMIT 1", n=nid) == 420
    # Identité résolue par whois (nom de connexion normalisé en minuscules), appareil partagé.
    assert scalar(stack, "SELECT count(*) FROM exit_node_identities WHERE exit_node_id = :n "
                         "AND login_name = :l AND is_sharee AND tailscale_user_id = 4242",
                  n=nid, l=CLIENT_A_LOGIN) == 1
    with stack["engine"].connect() as c:
        rows = c.execute(text(
            "SELECT tailscale_ip, resolution_status, identity_id IS NOT NULL FROM exit_node_observations "
            "WHERE exit_node_id = :n GROUP BY 1, 2, 3"), {"n": nid}).all()
    seen = {ip: (status, has_identity) for ip, status, has_identity in rows}
    assert seen[CLIENT_A_IP] == ("RESOLVED", True)
    assert seen[CLIENT_A_V6] == ("RESOLVED", True)
    assert seen[UNKNOWN_IP] == ("UNRESOLVED", False)
    assert "192.168.1.99" not in seen, "le trafic propre de l'Exit Node n'est pas une observation client"
    assert scalar(stack, "SELECT last_used_at IS NOT NULL FROM agent_credentials WHERE key_id = :k",
                  k=agent["key_id"])

    # Vue administrateur (lot 11d) par la vraie chaîne TLS → Nginx → backend.
    status, raw = Admin(stack).get_raw("/api/exit-nodes")
    assert status == 200
    page = json.loads(raw)
    node = next(n for n in page["items"] if n["name"] == NODE_NAME)
    assert node["healthy"] is True
    assert agent["key_id"] not in raw and agent["secret"] not in raw


def test_a_resent_batch_is_idempotent(stack, agent):
    nid = node_id(stack)
    with stack["engine"].connect() as c:
        batch_id, w1 = c.execute(text(
            "SELECT batch_id, window_end FROM agent_batches "
            "WHERE exit_node_id = :n ORDER BY received_at LIMIT 1"), {"n": nid}).one()
    before = scalar(stack, "SELECT count(*) FROM exit_node_observations WHERE exit_node_id = :n", n=nid)
    body = json.dumps({
        "schema": 1, "batch_id": str(batch_id), "sampled_at": w1.isoformat(), "window_seconds": 5,
        "conntrack_ok": True, "status_ok": True, "whois_ok": True, "observations": [],
    }).encode()
    reply = signed_post(agent, "/api/agent/batches", body)
    assert reply.status == 200, reply.body
    assert json.loads(reply.body)["status"] == "duplicate"
    assert scalar(stack, "SELECT count(*) FROM exit_node_observations WHERE exit_node_id = :n", n=nid) == before
    assert scalar(stack, "SELECT count(*) FROM agent_batches WHERE batch_id = :b", b=batch_id) == 1


def test_hmac_refusals_are_uniform_and_logged_without_secrets(stack, agent):
    ping = "/api/agent/ping"
    ok = signed_post(agent, ping, b"{}", nonce="f" * 32)
    assert ok.status == 200
    cases = {
        "replay": signed_post(agent, ping, b"{}", nonce="f" * 32),
        "bad_signature": signed_post(agent, ping, b"{}", secret="00" * 32),
        "clock_skew": signed_post(agent, ping, b"{}", now=time.time() - 600),
        "unknown_agent": signed_post(agent, ping, b"{}", key_id="agt_0000000000000000"),
        "unexpected_query": signed_post(agent, ping, b"{}", query="?x=1"),
    }
    bodies = set()
    for reason, reply in cases.items():
        assert reply.status == 401, (reason, reply.status, reply.body)
        bodies.add(reply.body)
    assert len(bodies) == 1, "tous les refus d'authentification renvoient la même réponse"
    big = signed_post(agent, "/api/agent/batches", b"x" * (1024 * 1024 + 1))
    assert big.status == 413

    log = backend_log(stack)
    for reason in cases:
        assert f"reason={reason}" in log, f"motif {reason} absent du journal"
    assert agent["secret"] not in log
    assert scalar(stack, "SELECT count(*) FROM audit_logs WHERE details::text LIKE :s",
                  s=f"%{agent['secret']}%") == 0


def test_an_ip_outside_agent_allowed_ips_is_refused(stack, fake_host, agent):
    """Même base, même identifiant valide, mais 127.0.0.1 n'est pas dans AGENT_ALLOWED_IPS."""
    port = free_port()
    other = Backend({**stack["backend"].env, "AGENT_ALLOWED_IPS": "100.64.0.0/10"}, port,
                    stack["work"] / "backend-ip.log").start()
    try:
        res = run_agent(fake_host, agent, "--ping", url=f"http://127.0.0.1:{port}")
        assert res.returncode == 1, res.stdout
        assert "ping=echec" in res.stdout
        assert "reason=ip_not_allowed ip=127.0.0.1" in (stack["work"] / "backend-ip.log").read_text()
    finally:
        other.stop()


def test_nginx_never_forwards_the_agent_api(stack, fake_host, agent):
    """Le chemin d'administration (Nginx, donc 8443) n'expose jamais /api/agent/ : 404 sans atteindre le backend."""
    before = backend_log(stack).count("POST /api/agent/ping")
    res = run_agent(fake_host, agent, "--ping", url=f"http://127.0.0.1:{stack['nginx_port']}")
    assert res.returncode == 1
    assert "ping=echec" in res.stdout
    reply = signed_post(agent, "/api/agent/ping", b"{}", url=f"http://127.0.0.1:{stack['nginx_port']}")
    assert reply.status == 404
    assert backend_log(stack).count("POST /api/agent/ping") == before


def test_backend_outage_is_absorbed_by_the_spool(stack, fake_host, agent):
    """Backend coupé : les lots s'accumulent dans le spool ; au retour, tout part, sans perte ni doublon."""
    nid = node_id(stack)
    batches_before = scalar(stack, "SELECT count(*) FROM agent_batches WHERE exit_node_id = :n", n=nid)
    spool = fake_host.base / "spool-outage" / "spool.db"
    spool.parent.mkdir()
    stack["backend"].stop()
    proc = spawn_agent(fake_host, agent, "--cycles", "12", "--interval", "1", "--window", "5", "--quiet",
                       "--spool", str(spool))
    try:
        time.sleep(3.5)
        from akwaba_agent.spool.store import Spool
        assert Spool(str(spool)).pending() >= 2, "les lots doivent attendre dans le spool"
    finally:
        stack["backend"].start()
    out, _ = proc.communicate(timeout=90)
    assert proc.returncode == 0, out
    s = summary(out)
    assert s["lots_ecrits"] == "12" and s["envoyes"] == "12" and s["refuses"] == "0", s
    from akwaba_agent.spool.store import Spool
    assert Spool(str(spool)).pending() == 0
    assert scalar(stack, "SELECT count(*) FROM agent_batches WHERE exit_node_id = :n", n=nid) == batches_before + 12
    # Le heartbeat a signalé la coupure puis le retour.
    with stack["engine"].connect() as c:
        details = c.execute(text("SELECT details FROM agent_heartbeats WHERE exit_node_id = :n ORDER BY id"),
                            {"n": nid}).scalars().all()
    assert any(d and d.get("send_failures", 0) > 0 for d in details), "les échecs d'envoi doivent remonter"


KNOWN_REPLAY_LIMITATION = (
    "Limitation connue (issue #5) : les nonces HMAC sont gardés en mémoire ; après un redémarrage du "
    "backend, un rejeu est accepté tant que l'horodatage reste dans la fenêtre de 120 s.")


def test_known_limitation_replay_after_backend_restart(stack):
    """Rejeu de la MÊME requête signée avant et après un redémarrage du backend.

    Les étapes préalables sont de vraies assertions. Seul le résultat final est déclaré « échec attendu »
    (xfail) tant que l'issue #5 n'est pas corrigée : il ne fait pas échouer la suite. Une fois la
    correction livrée, le rejeu renverra 401 et ce test passera sans modification.
    """
    res = _cli(stack, "create-agent", "--name", "exit-e2e-12a-rejeu", "--ip", "100.85.214.6")
    assert res.returncode == 0, res.stderr
    probe = {"key_id": re.search(r"key_id = (agt_[0-9a-f]{16})", res.stderr).group(1),
             "secret": res.stdout.strip(), "url": f"http://127.0.0.1:{stack['backend_port']}"}
    nonce, now = "a" * 32, time.time()                   # en-têtes identiques à chaque envoi
    assert signed_post(probe, "/api/agent/ping", b"{}", now=now, nonce=nonce).status == 200
    assert signed_post(probe, "/api/agent/ping", b"{}", now=now, nonce=nonce).status == 401
    stack["backend"].stop()
    stack["backend"].start()
    assert time.time() - now < 100, "le redémarrage doit rester dans la fenêtre de validité"
    status = signed_post(probe, "/api/agent/ping", b"{}", now=now, nonce=nonce).status
    if status == 200:
        pytest.xfail(KNOWN_REPLAY_LIMITATION)
    assert status == 401


def test_a_revoked_credential_is_refused_and_rotation_restores_service(stack, fake_host, agent):
    assert _cli(stack, "revoke-agent", "--key-id", agent["key_id"]).returncode == 0
    res = run_agent(fake_host, agent, "--ping")
    assert res.returncode == 1 and "ping=echec" in res.stdout
    assert "reason=revoked" in backend_log(stack)

    rotated = _cli(stack, "create-agent", "--name", NODE_NAME, "--ip", NODE_IP, "--rotate")
    assert rotated.returncode == 0, rotated.stderr
    new = dict(agent, key_id=re.search(r"key_id = (agt_[0-9a-f]{16})", rotated.stderr).group(1),
               secret=rotated.stdout.strip())
    new["secret_file"].write_text(new["secret"] + "\n")
    res = run_agent(fake_host, new, "--ping")
    assert res.returncode == 0 and f"ping=ok noeud={NODE_NAME}" in res.stdout
    with stack["engine"].connect() as c:
        actions = c.execute(text(
            "SELECT action FROM audit_logs WHERE entity_id IN (:a, :b) ORDER BY occurred_at, id"),
            {"a": agent["key_id"], "b": new["key_id"]}).scalars().all()
    assert actions == ["agent.credential_created", "agent.credential_revoked", "agent.credential_created"]


def test_dry_run_is_untouched(stack):
    """Rien dans ce lot ne produit de décision appliquée."""
    assert scalar(stack, "SELECT count(*) FROM enforcement_decisions WHERE mode <> 'dry_run' OR enforced") == 0
