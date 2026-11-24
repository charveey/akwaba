"""Lot 11c — identités, réaffectation atomique, signaux d'ambiguïté, vues de quarantaine.

PostgreSQL réel, horloge contrôlée, ingestion et réconciliation réelles (aucune action réseau :
toutes les décisions restent en dry_run, enforced=false).
"""
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.services import agent_admin
from app.services import members as members_svc
from app.services import quarantine as svc
from test_integration_quarantine import (  # noqa: F401  (fixtures réutilisées)
    H24, NOW, URL, ag, decisions, engine, heartbeat, identity_id, ingest, login, member_with, obs, records, rows,
    run, statuses,
)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")


def new_member(ag, name="Client", **extra):
    r = ag.c.post("/api/members", json={"full_name": name, **extra}, headers=ag.h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def link(ag, member, lg):
    r = ag.c.post(f"/api/members/{member}/identities", json={"login_name": lg}, headers=ag.h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def active_links(ag, lg):
    return rows(ag, "SELECT member_id, id FROM client_identities WHERE login_name = :l AND is_active", l=lg)


def ident(ag, lg):
    page = ag.c.get("/api/identities", params={"q": lg}).json()
    [item] = [i for i in page["items"] if i["login_name"] == lg]
    return item


# ----------------------------------------------------------------------- réaffectation atomique

def test_reassign_moves_the_login_in_one_transaction_with_explicit_audit(ag):
    lg = login()
    a, b = new_member(ag, "Ancien"), new_member(ag, "Nouveau")
    old_link = link(ag, a, lg)
    ingest(ag, [obs(lg)])
    r = ag.c.post(f"/api/members/{a}/identities/{old_link}/reassign", json={"target_member_id": b}, headers=ag.h)
    assert r.status_code == 200, r.text
    new_link = r.json()["id"]
    assert [(str(m), str(i)) for m, i in active_links(ag, lg)] == [(b, new_link)]
    [(active, unlinked)] = rows(ag, "SELECT is_active, unlinked_at FROM client_identities WHERE id = :i", i=old_link)
    assert active is False and unlinked == NOW                     # historique conservé
    [(action, details)] = rows(ag, "SELECT action, details FROM audit_logs WHERE action = 'identity.reassigned'")
    assert details == {"login_name": lg, "from_member_id": a, "to_member_id": b,
                       "from_client_identity_id": old_link, "to_client_identity_id": new_link}
    assert ident(ag, lg)["member"]["id"] == b


@pytest.mark.parametrize("case, expected", [
    ("same", 409), ("archived", 409), ("missing_target", 404), ("wrong_source", 404), ("inactive", 409),
    ("missing_identity", 404),
])
def test_reassign_errors_leave_everything_unchanged(ag, case, expected):
    lg = login()
    a, b, other = new_member(ag, "A"), new_member(ag, "B"), new_member(ag, "Autre")
    link_id = link(ag, a, lg)
    src, target, ident_id = a, b, link_id
    if case == "same":
        target = a
    elif case == "archived":
        assert ag.c.post(f"/api/members/{b}/archive", headers=ag.h).status_code == 200
    elif case == "missing_target":
        target = str(uuid.uuid4())
    elif case == "wrong_source":
        src = other
    elif case == "inactive":
        assert ag.c.post(f"/api/members/{a}/identities/{link_id}/unlink", headers=ag.h).status_code == 200
    elif case == "missing_identity":
        ident_id = str(uuid.uuid4())
    before_links = rows(ag, "SELECT id, member_id, is_active FROM client_identities WHERE login_name = :l ORDER BY id", l=lg)
    before_audit = rows(ag, "SELECT count(*) FROM audit_logs")[0][0]
    r = ag.c.post(f"/api/members/{src}/identities/{ident_id}/reassign", json={"target_member_id": target}, headers=ag.h)
    assert r.status_code == expected, r.text
    assert rows(ag, "SELECT id, member_id, is_active FROM client_identities WHERE login_name = :l ORDER BY id", l=lg) == before_links
    assert rows(ag, "SELECT count(*) FROM audit_logs")[0][0] == before_audit


def test_reassign_rolls_back_completely_when_a_step_fails(ag, monkeypatch):
    lg = login()
    a, b = new_member(ag, "A"), new_member(ag, "B")
    link_id = link(ag, a, lg)

    def boom(*args, **kwargs):
        raise RuntimeError("panne simulée pendant l'audit")

    monkeypatch.setattr(members_svc, "record", boom)
    admin = rows(ag, "SELECT id FROM admin_users WHERE id = :i", i=ag.admin_id)[0][0]
    ctx = type("Ctx", (), {"admin": type("A", (), {"id": admin})()})()
    from app.domain.clock import FixedClock
    with ag.maker() as db, pytest.raises(RuntimeError):
        members_svc.reassign_identity(db, ctx, FixedClock(NOW), uuid.UUID(a), uuid.UUID(link_id), uuid.UUID(b), None)
    assert [(str(m), str(i)) for m, i in active_links(ag, lg)] == [(a, link_id)]
    assert rows(ag, "SELECT count(*) FROM client_identities WHERE login_name = :l", l=lg)[0][0] == 1


def test_reassign_requires_admin_csrf_and_origin(ag):
    lg = login()
    a, b = new_member(ag, "A"), new_member(ag, "B")
    link_id = link(ag, a, lg)
    path, body = f"/api/members/{a}/identities/{link_id}/reassign", {"target_member_id": b}
    assert ag.c.post(path, json=body).status_code == 403                                           # sans jeton
    assert ag.c.post(path, json=body, headers={**ag.h, "Origin": "https://evil.example"}).status_code == 403
    assert ag.c.post(path, json={**body, "extra": 1}, headers=ag.h).status_code == 422
    with TestClient(create_app(), base_url="https://testserver", headers={"Origin": "https://testserver"}) as anon:
        assert anon.post(path, json=body).status_code == 401
    assert [(str(m), str(i)) for m, i in active_links(ag, lg)] == [(a, link_id)]


# ----------------------------------------------------------------------- signaux d'ambiguïté

def test_archived_member_login_signal(ag):
    lg = login()
    m = new_member(ag, "Archivé")
    link(ag, m, lg)
    assert ag.c.post(f"/api/members/{m}/archive", headers=ag.h).status_code == 200
    ingest(ag, [obs(lg)])
    item = ident(ag, lg)
    assert item["ambiguity"] == ["ARCHIVED_MEMBER_LOGIN"]
    assert item["member"] is None and item["verdict"] == "UNKNOWN_IDENTITY"   # verdict inchangé


def test_contact_email_match_is_a_suggestion_only(ag):
    lg = login()
    m = new_member(ag, "Contact", email=lg)
    ingest(ag, [obs(lg)])
    item = ident(ag, lg)
    assert item["ambiguity"] == ["CONTACT_EMAIL_MATCH"] and item["member"] is None
    detail = ag.c.get(f"/api/identities/{item['id']}").json()
    assert detail["contact_matches"] == [{"id": m, "full_name": "Contact"}]
    assert active_links(ag, lg) == []                                         # jamais d'association automatique
    run(ag)
    assert statuses(ag, lg) == ["PENDING"]                                    # évaluée comme inconnue, normalement
    link(ag, m, lg)
    assert ident(ag, lg)["ambiguity"] == []                                   # rattachée au bon membre : plus de signal


def test_multiple_exit_nodes_counts_distinct_nodes_not_observations(ag):
    lg = login()
    for _ in range(3):
        ingest(ag, [obs(lg)])
    item = ident(ag, lg)
    assert item["exit_node_count"] == 1 and "MULTIPLE_EXIT_NODES" not in item["ambiguity"]
    second = f"n2-{uuid.uuid4().hex[:6]}"
    with ag.maker() as db:
        agent_admin.create_agent(db, name=second, ip="100.85.214.6", rotate=False, now=NOW)
    node2 = rows(ag, "SELECT id FROM exit_nodes WHERE name = :n", n=second)[0][0]
    original = ag.node_id
    ag.node_id = node2
    ingest(ag, [obs(lg)])
    ag.node_id = original
    page = ag.c.get("/api/identities", params={"q": lg}).json()
    items = [i for i in page["items"] if i["login_name"] == lg]
    assert len(items) == 2                                                    # une identité par Exit Node
    assert all(i["exit_node_count"] == 2 and "MULTIPLE_EXIT_NODES" in i["ambiguity"] for i in items)
    assert page["exit_node_reference_days"] == 90
    assert ag.c.get("/api/identities", params={"q": lg, "ambiguous": "true"}).json()["total"] == 2


# ----------------------------------------------------------------------- UNRESOLVED : jamais actionnable

def test_unresolved_sources_never_create_identity_record_or_decision(ag):
    identities_before = rows(ag, "SELECT count(*) FROM exit_node_identities")[0][0]
    o = obs(login())
    o.update({"identity_status": "UNRESOLVED", "identity": None, "src_ip": "100.99.1.2"})
    ingest(ag, [o])
    ingest(ag, [dict(o)])
    for hours in (0, 25, 49):
        ag.clk[0] = NOW + timedelta(hours=hours)
        heartbeat(ag, ag.clk[0])
        run(ag)
    # Aucune identité forcée : les observations non résolues n'ont pas d'identité…
    assert rows(ag, "SELECT count(*) FROM exit_node_identities")[0][0] == identities_before
    assert rows(ag, "SELECT count(*), count(identity_id) FROM exit_node_observations "
                    "WHERE tailscale_ip = '100.99.1.2'")[0] == (2, 0)
    # … et tout dossier ou décision existant se rattache à une identité observée RÉSOLUE.
    orphan = rows(ag, """
        SELECT count(*) FROM quarantine_records r WHERE NOT EXISTS (
          SELECT 1 FROM exit_node_observations o WHERE o.identity_id = r.exit_node_identity_id
            AND o.resolution_status = 'RESOLVED')""")[0][0]
    orphan_dec = rows(ag, """
        SELECT count(*) FROM enforcement_decisions d WHERE NOT EXISTS (
          SELECT 1 FROM exit_node_observations o WHERE o.identity_id = d.exit_node_identity_id
            AND o.resolution_status = 'RESOLVED')""")[0][0]
    assert (orphan, orphan_dec) == (0, 0)
    ag.clk[0] = NOW
    page = ag.c.get("/api/identities/unresolved", params={"hours": 24}).json()
    [src] = [s for s in page["items"] if s["tailscale_ip"] == "100.99.1.2"]
    assert src["observations"] == 2 and set(src) == {"exit_node_id", "tailscale_ip", "observations",
                                                     "first_observed_at", "last_observed_at"}


# ----------------------------------------------------------------------- vues des dossiers

def record_view(ag, lg):
    iid = identity_id(ag, lg)
    return ag.c.get("/api/quarantine", params={"identity_id": str(iid)}).json()["items"]


def test_pending_record_view_has_server_side_remaining_time_and_current_verdict(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    [rec] = record_view(ag, lg)
    assert (rec["status"], rec["remaining_seconds"], rec["current_verdict"]) == ("PENDING", 86400, "UNKNOWN_IDENTITY")
    assert rec["suppressed_until"] is None and rec["last_confirmed_activity_at"] is not None
    ag.clk[0] = NOW + timedelta(hours=23)
    assert record_view(ag, lg)[0]["remaining_seconds"] == 3600
    ag.clk[0] = NOW + H24
    heartbeat(ag, ag.clk[0])
    run(ag)
    [rec] = record_view(ag, lg)
    assert rec["status"] == "EXPIRED" and rec["simulated_block"] is True and rec["remaining_seconds"] is None
    dec = ag.c.get("/api/enforcement-decisions", params={"record_id": rec["id"]}).json()["items"]
    assert [(d["decision"], d["mode"], d["enforced"]) for d in dec] == [("WOULD_BLOCK", "dry_run", False)]
    assert ag.c.get("/api/enforcement-decisions", params={"identity_id": rec["exit_node_identity_id"]}).json()["total"] == 1


def test_manual_review_record_never_blocks_and_has_no_countdown(ag):
    lg = login()
    m = new_member(ag, "Sans abonnement")
    link(ag, m, lg)
    ingest(ag, [obs(lg)])
    run(ag)
    for hours in (25, 72):
        ag.clk[0] = NOW + timedelta(hours=hours)
        heartbeat(ag, ag.clk[0])
        ingest(ag, [obs(lg)])
        run(ag)
    [rec] = record_view(ag, lg)
    assert rec["status"] == "MANUAL_REVIEW" and rec["remaining_seconds"] is None
    assert rec["current_verdict"] == "MANUAL_REVIEW" and decisions(ag, lg) == []


def test_dismissed_record_view_and_reopening_exactly_at_seven_days(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    [rec] = record_view(ag, lg)
    r = ag.c.post(f"/api/quarantine/{rec['id']}/dismiss", json={"note": "test écarté"}, headers=ag.h)
    assert r.status_code == 200
    [rec] = record_view(ag, lg)
    assert rec["status"] == "DISMISSED" and rec["reopen_possible"] is False
    assert rec["suppressed_until"].startswith((NOW + timedelta(days=7)).isoformat()[:19])
    assert rec["resolved_by_email"].endswith("@example.test")
    ag.clk[0] = NOW + timedelta(days=7) - timedelta(seconds=1)
    heartbeat(ag, ag.clk[0])
    ingest(ag, [obs(lg)])
    run(ag)
    assert statuses(ag, lg) == ["DISMISSED"]
    ag.clk[0] = NOW + timedelta(days=7)                                       # borne exacte
    heartbeat(ag, ag.clk[0])
    ingest(ag, [obs(lg)])
    run(ag)
    assert statuses(ag, lg) == ["DISMISSED", "PENDING"]
    dismissed = [x for x in record_view(ag, lg) if x["status"] == "DISMISSED"][0]
    assert dismissed["reopen_possible"] is True


def test_authorizing_creates_exemption_with_note_as_reason(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    [rec] = record_view(ag, lg)
    assert ag.c.post(f"/api/quarantine/{rec['id']}/authorize", json={"note": "invité de passage"}, headers=ag.h).status_code == 200
    ex = ag.c.get("/api/identity-exemptions", params={"identity_id": rec["exit_node_identity_id"]}).json()["items"]
    assert len(ex) == 1 and ex[0]["reason"] == "invité de passage" and ex[0]["active"] is True
    assert ex[0]["granted_by_email"].endswith("@example.test") and ex[0]["ended_by_email"] is None
    item = ident(ag, lg)
    assert item["exempt"] is True and item["open_record"] is None


def test_manual_protection_prevents_any_record_and_is_reported_by_source(ag):
    lg = login()
    r = ag.c.post("/api/protected-identities", json={"login_name": lg, "reason": "administrateur"}, headers=ag.h)
    assert r.status_code == 201
    ingest(ag, [obs(lg)])
    run(ag)
    item = ident(ag, lg)
    assert item["verdict"] == "PROTECTED" and item["protection_sources"] == ["MANUAL"] and statuses(ag, lg) == []
    assert ag.c.post(f"/api/protected-identities/{r.json()['id']}/remove", headers=ag.h).status_code == 200
    item = ident(ag, lg)
    assert item["verdict"] == "UNKNOWN_IDENTITY" and item["protection_sources"] == []


def test_identity_detail(ag):
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    iid = identity_id(ag, lg)
    d = ag.c.get(f"/api/identities/{iid}").json()
    assert d["identity"]["login_name"] == lg and d["identity"]["tailscale_user_id"] == 7
    assert d["identity"]["open_record"]["status"] == "PENDING"
    assert len(d["observations"]) == 1 and d["observations"][0]["active_confirmed"] is True
    assert [r["status"] for r in d["records"]] == ["PENDING"] and d["decisions"] == [] and d["exemptions"] == []
    assert ag.c.get(f"/api/identities/{uuid.uuid4()}").status_code == 404
    assert ag.c.get("/api/identities/pas-un-uuid").status_code == 422


def test_all_decisions_stay_simulated(ag):
    for _ in range(2):
        lg = login()
        ingest(ag, [obs(lg)])
        run(ag)
    ag.clk[0] = NOW + H24
    heartbeat(ag, ag.clk[0])
    run(ag)
    rows_ = rows(ag, "SELECT mode, enforced FROM enforcement_decisions")
    assert rows_ and all(r == ("dry_run", False) for r in rows_)
