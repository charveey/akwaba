"""Lot 11d — aucun parcours d'écriture n'enregistre de secret, mot de passe, jeton ou cookie dans l'audit.

Parcours exercés : connexions (réussie, échouée, compte inconnu), déconnexion, membres, identités,
réaffectation, abonnements, paiements (et annulation), dépenses (création, correction, annulation),
catégories, relances, protections, quarantaine (ouverture, autorisation, exemption), création / rotation /
révocation d'identifiants d'agent (secret HMAC en clair connu du test).
"""
import json
import re
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.services import agent_admin
from app.services import quarantine as svc
from test_integration_quarantine import (  # noqa: F401
    NOW, PASSWORD, URL, ag, engine, heartbeat, ingest, login, member_with, obs, records, rows, run,
)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
SENSITIVE_KEY = re.compile(r"pass(word|wd)?|secret|token|csrf|api[_-]?key|cookie|authorization|hmac|signature", re.I)


def keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from keys(v)


def test_no_write_path_records_secrets(ag):
    secrets = {PASSWORD, ag.h["X-CSRF-Token"]}
    # Connexions
    email = rows(ag, "SELECT email FROM admin_users WHERE id = :i", i=ag.admin_id)[0][0]
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app()
    app.dependency_overrides.update(ag.c.app.dependency_overrides)       # même base, même horloge
    with TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"}) as c:
        c.post("/api/auth/login", json={"email": email, "password": "mot-de-passe-faux-123"})
        c.post("/api/auth/login", json={"email": "personne@example.test", "password": "autre-faux-456"})
        r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        secrets |= {r.json()["csrf_token"], r.cookies.get("akwaba_session") or ""}
        c.post("/api/auth/logout", headers={"X-CSRF-Token": r.json()["csrf_token"]})
    secrets |= {"mot-de-passe-faux-123", "autre-faux-456"}
    # Membres, identités, abonnements, paiements
    lg = login()
    m = member_with(ag, lg)
    other = ag.c.post("/api/members", json={"full_name": "Cible", "phone_e164": "+2250102030405"}, headers=ag.h).json()["id"]
    link = rows(ag, "SELECT id FROM client_identities WHERE login_name = :l AND is_active", l=lg)[0][0]
    ag.c.post(f"/api/members/{m}/identities/{link}/reassign", json={"target_member_id": other}, headers=ag.h)
    pay = rows(ag, "SELECT p.id FROM payments p JOIN subscriptions s ON s.id = p.subscription_id WHERE s.member_id = :m", m=m)[0][0]
    ag.c.post(f"/api/payments/{pay}/void", json={"reason": "test"}, headers=ag.h)
    # Dépenses et catégories
    cat = ag.c.post("/api/expense-categories", json={"name": "Catégorie test audit"}, headers=ag.h).json()["id"]
    e = ag.c.post("/api/expenses", json={"category_id": cat, "amount": 10, "expense_date": "2039-12-01"}, headers=ag.h).json()
    e2 = ag.c.post(f"/api/expenses/{e['id']}/correct", json={"category_id": cat, "amount": 11, "expense_date": "2039-12-01"}, headers=ag.h).json()
    ag.c.post(f"/api/expenses/{e2['id']}/void", json={"reason": "test"}, headers=ag.h)
    # Relances, protections
    ag.c.post("/api/reminders/generate", headers=ag.h)
    p = ag.c.post("/api/protected-identities", json={"login_name": "proteg@example.test"}, headers=ag.h).json()
    ag.c.post(f"/api/protected-identities/{p['id']}/remove", headers=ag.h)
    # Quarantaine
    q = login()
    ingest(ag, [obs(q)])
    run(ag)
    rid = records(ag, q)[0][-1]
    ag.c.post(f"/api/quarantine/{rid}/authorize", json={"note": "ok"}, headers=ag.h)
    # Identifiants d'agent : secret HMAC en clair connu ici
    with ag.maker() as db:
        key_id, secret = agent_admin.create_agent(db, name="audit-secrets", ip="100.85.214.9", rotate=False, now=NOW)
        key2, secret2 = agent_admin.create_agent(db, name="audit-secrets", ip="100.85.214.9", rotate=True, now=NOW)
        agent_admin.revoke_agent(db, key2, now=NOW)
    secrets |= {secret, secret2}
    secrets |= set(rows(ag, "SELECT secret_encrypted FROM agent_credentials")[0])

    audit = rows(ag, "SELECT action, details FROM audit_logs")
    assert len(audit) > 20
    actions = {a for a, _ in audit}
    for expected in ("admin.login_failed", "admin.login_success", "admin.logout", "identity.reassigned",
                     "payment.voided", "expense.corrected", "protected_identity.added", "quarantine.authorized"):
        assert expected in actions, expected
    for action, details in audit:
        blob = json.dumps(details or {}, ensure_ascii=False)
        assert not [k for k in keys(details) if SENSITIVE_KEY.search(str(k))], (action, details)
        for s in secrets:
            assert not s or s not in blob, (action, "valeur secrète trouvée")
