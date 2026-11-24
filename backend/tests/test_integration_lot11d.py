"""Lot 11d — vue d'ensemble, rapport opérationnel, CSV, correction de dépense, diagnostic de l'agent,
audit normalisé. PostgreSQL réel, horloge contrôlée (NOW = 2040-01-01 12:00 UTC)."""
import csv
import io
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.services import finance as finance_svc
from app.services import quarantine as qsvc
from test_integration_quarantine import (  # noqa: F401
    H24, NOW, PASSWORD, URL, ag, engine, heartbeat, ingest, login, member_with, obs, rows, run,
)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")


def parse_csv(r):
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith("attachment;")
    body = r.content.decode("utf-8")
    assert body.startswith("﻿")
    return list(csv.reader(io.StringIO(body[1:]), delimiter=";"))


def category(ag, name="Hébergement"):
    return [c for c in ag.c.get("/api/expense-categories").json() if c["name"] == name][0]["id"]


def add_expense(ag, amount, day="2039-12-15", name="Hébergement", description=None):
    r = ag.c.post("/api/expenses", json={"category_id": category(ag, name), "amount": amount,
                                         "expense_date": day, "description": description}, headers=ag.h)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------------- vue d'ensemble

def test_dashboard_indicators_match_independent_sql(ag):
    paid = member_with(ag, login())                                  # actif
    pending_member = ag.c.post("/api/members", json={"full_name": "Attente"}, headers=ag.h).json()["id"]
    assert ag.c.post("/api/subscriptions", json={"member_id": pending_member, "plan_id": ag.plans["M3"]},
                     headers=ag.h).status_code == 201
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)                                                          # un dossier PENDING
    heartbeat(ag, NOW)
    d = ag.c.get("/api/dashboard").json()
    i = d["indicators"]
    sql = rows(ag, """SELECT
        (SELECT count(*) FROM members WHERE archived_at IS NULL),
        (SELECT count(*) FROM subscriptions s JOIN members m ON m.id = s.member_id
           WHERE s.payment_status = 'PENDING' AND s.canceled_at IS NULL AND m.archived_at IS NULL),
        (SELECT coalesce(sum(coalesce(settled_amount, amount)), 0) FROM payments
           WHERE status = 'CONFIRMED' AND paid_at >= '2040-01-01T00:00:00Z' AND paid_at < '2040-02-01T00:00:00Z'),
        (SELECT count(*) FROM quarantine_records WHERE status IN ('PENDING','EXPIRED','PENDING_REVOCATION','MANUAL_REVIEW')),
        (SELECT max(received_at) FROM agent_heartbeats)""")[0]
    assert (i["members_total"], i["payments_pending"], i["revenue_month_xof"], i["quarantine_open"]) == sql[:4]
    assert i["members_active"] >= 1 and i["revenue_month_xof"] > 0
    assert i["agent"]["last_heartbeat_at"].startswith(sql[4].isoformat()[:19])
    assert i["agent"]["healthy_nodes"] >= 1
    assert d["today"] == "2040-01-01" and d["month_start"] == "2040-01-01" and d["range_start"] == "2039-02-01"
    assert [r["label"] for r in d["revenue_by_month"]][0] == "2039-02" and len(d["revenue_by_month"]) == 12
    assert d["revenue_by_month"][-1]["amount_xof"] == i["revenue_month_xof"]
    assert sum(p["count"] for p in d["subscriptions_by_plan"]) == i["members_active"] + i["members_grace"]
    assert [s["status"] for s in d["quarantine_by_status"]] == [
        "PENDING", "AUTHORIZED", "EXPIRED", "PENDING_REVOCATION", "MANUAL_REVIEW", "DISMISSED"]
    m = d["members_by_month"]
    assert len(m) == 12 and m[-1]["cumulative"] == rows(ag, "SELECT count(*) FROM members")[0][0]
    assert paid


def test_dashboard_requires_admin(engine):
    with TestClient(create_app(), base_url="https://testserver", headers={"Origin": "https://testserver"}) as anon:
        for path in ("/api/dashboard", "/api/exit-nodes", "/api/reports/operations", "/api/audit"):
            assert anon.get(path).status_code == 401


# ------------------------------------------------------------------------- rapport opérationnel

def test_operations_report_snapshot_flows_and_csv(ag):
    member_with(ag, login())
    lg = login()
    ingest(ag, [obs(lg)])
    run(ag)
    r = ag.c.get("/api/reports/operations", params={"date_from": "2039-12-01", "date_to": "2040-01-31"}).json()
    assert [x["label"] for x in r["rows"]] == ["2039-12", "2040-01"]
    jan = r["rows"][1]
    assert jan["new_member_subscriptions"] >= 1 and jan["quarantine_opened"] >= 1 and jan["identities_first_seen"] >= 1
    assert set(r["snapshot"]) == {"ACTIVE", "GRACE_PERIOD", "EXPIRED", "SUSPENDED", "NONE"}
    assert sum(r["snapshot"].values()) == r["members_total"]
    assert r["totals"]["quarantine_opened"] == sum(x["quarantine_opened"] for x in r["rows"])
    table = parse_csv(ag.c.get("/api/reports/operations", params={"date_from": "2039-12-01", "date_to": "2040-01-31",
                                                                  "format": "csv"}))
    assert table[0][0] == "mois" and [row[0] for row in table[1:]] == ["2039-12", "2040-01"]
    assert ag.c.get("/api/reports/operations", params={"date_from": "2040-02-01", "date_to": "2040-01-01"}).status_code == 422


def test_financial_report_csv_matches_json(ag):
    member_with(ag, login())
    add_expense(ag, 5000, "2040-01-01")
    for path, params in [("/api/reports/revenue", {"group_by": "payment_method"}),
                         ("/api/reports/revenue-by-plan", {}),
                         ("/api/reports/expenses", {"group_by": "category"}),
                         ("/api/reports/profit", {"group_by": "year"})]:
        js = ag.c.get(path, params=params).json()
        table = parse_csv(ag.c.get(path, params={**params, "format": "csv"}))
        assert len(table) - 1 == len(js["rows"]), path
    profit = parse_csv(ag.c.get("/api/reports/profit", params={"group_by": "year", "format": "csv"}))
    assert profit[0] == ["annee", "recettes_xof", "depenses_xof", "resultat_xof"]


# ------------------------------------------------------------------------- dépenses

def test_expense_csv_contains_all_filtered_rows_and_neutralizes_formulas(ag):
    for n in range(3):
        add_expense(ag, 100 + n, "2039-12-1%d" % n, description="=HYPERLINK(\"http://x\")" if n == 0 else "ok")
    page = ag.c.get("/api/expenses", params={"limit": 1, "date_from": "2039-12-10", "date_to": "2039-12-12"}).json()
    assert len(page["items"]) == 1 and page["total"] == 3
    table = parse_csv(ag.c.get("/api/expenses", params={"limit": 1, "date_from": "2039-12-10", "date_to": "2039-12-12",
                                                        "format": "csv"}))
    assert len(table) == 4                                                   # en-tête + 3 lignes (pas la page)
    assert any(cell.startswith("'=HYPERLINK") for row in table for cell in row)


def test_correct_expense_is_atomic_and_linked(ag):
    old = add_expense(ag, 1000, description="loyer")
    r = ag.c.post(f"/api/expenses/{old['id']}/correct", headers=ag.h, json={
        "category_id": category(ag, "Marketing"), "amount": 1200, "expense_date": "2039-12-16", "description": "loyer corrigé"})
    assert r.status_code == 201, r.text
    new = r.json()
    [(status, reason)] = rows(ag, "SELECT status, void_reason FROM expenses WHERE id = :i", i=old["id"])
    assert (status, reason) == ("VOIDED", "Corrigée")
    assert new["amount"] == 1200 and new["category_name"] == "Marketing" and new["status"] == "ACTIVE"
    [(details,)] = rows(ag, "SELECT details FROM audit_logs WHERE action = 'expense.corrected'")
    assert details["old_expense_id"] == old["id"] and details["new_expense_id"] == new["id"]
    assert "description" not in details                                     # aucun texte libre dans l'audit
    items = {e["id"]: e for e in ag.c.get("/api/expenses", params={"date_from": "2039-12-15", "date_to": "2039-12-16"}).json()["items"]}
    assert items[old["id"]]["replaced_by_id"] == new["id"] and items[new["id"]]["replaces_id"] == old["id"]
    assert ag.c.get(f"/api/expenses/{old['id']}").json()["status"] == "VOIDED"      # ancienne toujours consultable


@pytest.mark.parametrize("case, expected", [("voided", 409), ("missing", 404), ("future", 422), ("bad_category", 404)])
def test_correct_expense_errors_change_nothing(ag, case, expected):
    old = add_expense(ag, 700)
    eid, body = old["id"], {"category_id": category(ag), "amount": 800, "expense_date": "2039-12-20"}
    if case == "voided":
        assert ag.c.post(f"/api/expenses/{eid}/void", json={"reason": "x"}, headers=ag.h).status_code == 200
    elif case == "missing":
        eid = str(uuid.uuid4())
    elif case == "future":
        body["expense_date"] = "2040-01-02"
    elif case == "bad_category":
        body["category_id"] = str(uuid.uuid4())
    before = rows(ag, "SELECT id, status FROM expenses ORDER BY id")
    r = ag.c.post(f"/api/expenses/{eid}/correct", json=body, headers=ag.h)
    assert r.status_code == expected, r.text
    assert rows(ag, "SELECT id, status FROM expenses ORDER BY id") == before


def test_correct_expense_rolls_back_completely(ag, monkeypatch):
    old = add_expense(ag, 900)

    def boom(*a, **k):
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(finance_svc, "record", boom)
    before = rows(ag, "SELECT id, status FROM expenses ORDER BY id")
    with pytest.raises(RuntimeError):
        ag.c.post(f"/api/expenses/{old['id']}/correct", headers=ag.h,
                  json={"category_id": category(ag), "amount": 950, "expense_date": "2039-12-20"})
    assert rows(ag, "SELECT id, status FROM expenses ORDER BY id") == before


def test_immutability_trigger_still_active(ag):
    old = add_expense(ag, 300)
    with pytest.raises(Exception) as exc:
        with ag.engine.begin() as c:
            c.execute(text("UPDATE expenses SET amount = 1 WHERE id = :i"), {"i": old["id"]})
    assert "immuables" in str(exc.value)


def test_correct_expense_requires_csrf_origin_admin(ag):
    old = add_expense(ag, 400)
    path, body = f"/api/expenses/{old['id']}/correct", {"category_id": category(ag), "amount": 1, "expense_date": "2039-12-20"}
    assert ag.c.post(path, json=body).status_code == 403
    assert ag.c.post(path, json=body, headers={**ag.h, "Origin": "https://evil.example"}).status_code == 403
    with TestClient(create_app(), base_url="https://testserver", headers={"Origin": "https://testserver"}) as anon:
        assert anon.post(path, json=body).status_code == 401
    assert rows(ag, "SELECT status FROM expenses WHERE id = :i", i=old["id"]) == [("ACTIVE",)]


def test_category_usage_count(ag):
    add_expense(ag, 10, name="Autre")
    cats = {c["name"]: c for c in ag.c.get("/api/expense-categories", params={"include_inactive": "true"}).json()}
    assert cats["Autre"]["expense_count"] >= 1 and "expense_count" in cats["Marketing"]


# ------------------------------------------------------------------------- diagnostic de l'agent

def test_exit_node_diagnostic_uses_reconciler_health_and_hides_secrets(ag, monkeypatch):
    heartbeat(ag, NOW - timedelta(minutes=1))
    ingest(ag, [obs(login())])
    page = ag.c.get("/api/exit-nodes").json()
    node = [n for n in page["items"] if n["id"] == str(ag.node_id)][0]
    assert node["healthy"] is True and page["health_max_age_seconds"] == 300
    assert node["credentials"]["active"] == 1
    calls = []
    real = qsvc._health
    monkeypatch.setattr(qsvc, "_health", lambda *a, **k: calls.append(a) or {ag.node_id: False})
    assert [n for n in ag.c.get("/api/exit-nodes").json()["items"] if n["id"] == str(ag.node_id)][0]["healthy"] is False
    assert calls                                                            # la fonction du réconciliateur est utilisée
    monkeypatch.setattr(qsvc, "_health", real)
    detail = ag.c.get(f"/api/exit-nodes/{ag.node_id}").json()
    assert len(detail["heartbeats"]) >= 1 and len(detail["batches"]) >= 1 and detail["batches"][0]["observations"] == 1
    raw = ag.c.get(f"/api/exit-nodes/{ag.node_id}").text
    key_id, secret_enc = rows(ag, "SELECT key_id, secret_encrypted FROM agent_credentials WHERE exit_node_id = :n",
                              n=ag.node_id)[0]
    assert key_id not in raw and secret_enc not in raw
    assert "key_id" not in raw and "secret" not in raw.lower()
    assert ag.c.get(f"/api/exit-nodes/{uuid.uuid4()}").status_code == 404


def test_exit_node_offsets_and_last_error(ag):
    with ag.engine.begin() as c:
        c.execute(text(
            "INSERT INTO agent_heartbeats (exit_node_id, received_at, agent_version, protocol_version, conntrack_ok, "
            "status_ok, whois_ok, bytes_available, spool_depth, last_error, details) VALUES (:n, :t, '1.0', 1, true, "
            "true, false, false, 4, 'whois_timeout', CAST(:d AS jsonb))"),
            {"n": ag.node_id, "t": NOW, "d": '{"generated_at": "2040-01-01T11:59:58+00:00", "clock_skew_seconds": -2, '
             '"conntrack_saturation": 0.42, "spool_oldest_age_seconds": 30, "last_send_error": "timeout", "secret": "x"}'})
    node = [n for n in ag.c.get("/api/exit-nodes").json()["items"] if n["id"] == str(ag.node_id)][0]
    hb = node["last_heartbeat"]
    assert (hb["clock_skew_seconds"], hb["server_offset_seconds"], hb["conntrack_saturation"], hb["spool_depth"]) == (-2, 2.0, 0.42, 4)
    assert node["healthy"] is False                                         # whois_ok = false (règle du réconciliateur)
    assert node["last_error"]["error"] == "whois_timeout" and node["last_error"]["send_error"] == "timeout"
    assert "secret" not in hb                                               # liste blanche : clé inconnue ignorée


# ------------------------------------------------------------------------- audit

def test_failed_login_is_never_attributed_to_the_target_account(ag):
    email = rows(ag, "SELECT email FROM admin_users WHERE id = :i", i=ag.admin_id)[0][0]
    app = create_app()
    app.dependency_overrides.update(ag.c.app.dependency_overrides)       # même base, même horloge
    with TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"}) as c:
        assert c.post("/api/auth/login", json={"email": email, "password": "mauvais"}).status_code == 401
        assert c.post("/api/auth/login", json={"email": "inconnu@example.test", "password": "x"}).status_code == 401
    items = ag.c.get("/api/audit", params={"action": "admin.login_failed"}).json()["items"]
    kinds = {(e["actor_kind"], e["actor_display"]) for e in items}
    assert ("attempt", f"Tentative de connexion sur le compte {email} (non authentifiée)") in kinds
    assert ("attempt", "Tentative de connexion (non authentifiée)") in kinds
    assert all(e["actor_kind"] == "attempt" for e in items)
    ok = ag.c.get("/api/audit", params={"action": "admin.login_success"}).json()["items"][0]
    assert (ok["actor_kind"], ok["actor_display"]) == ("admin", ok["actor_email"])
    run(ag)
    auto = ag.c.get("/api/audit", params={"entity_type": "quarantine_record"}).json()["items"]
    assert all(e["actor_kind"] == "system" for e in auto if e["action"] in ("quarantine.opened", "quarantine.changed"))


def test_audit_actor_comes_from_the_session_not_the_browser(ag):
    r = ag.c.post("/api/members", json={"full_name": "Session", "actor_admin_id": str(uuid.uuid4())}, headers=ag.h)
    assert r.status_code == 422                                             # champ inconnu refusé (extra=forbid)
    r = ag.c.post("/api/members", json={"full_name": "Session"},
                  headers={**ag.h, "X-Actor": "pirate@example.test", "X-Admin-Id": str(uuid.uuid4())})
    mid = r.json()["id"]
    [(actor, label)] = rows(ag, "SELECT actor_admin_id, actor_label FROM audit_logs WHERE action = 'member.created' "
                                "AND entity_id = :e", e=mid)
    assert actor == ag.admin_id and label is None


def test_audit_reading_masks_sensitive_keys_recursively(ag):
    with ag.engine.begin() as c:
        c.execute(text("INSERT INTO audit_logs (actor_label, action, details) VALUES ('system', 'test.masque', "
                       "CAST(:d AS jsonb))"), {"d": '{"a": 1, "token": "t", "x": [{"password": "p"}, {"y": {"secret": "s"}}]}'})
    [e] = ag.c.get("/api/audit", params={"action": "test.masque"}).json()["items"]
    assert e["details"] == {"a": 1, "token": "[masqué]", "x": [{"password": "[masqué]"}, {"y": {"secret": "[masqué]"}}]}
    # Le stockage n'est jamais réécrit (append-only).
    assert rows(ag, "SELECT details->>'token' FROM audit_logs WHERE action = 'test.masque'") == [("t",)]
