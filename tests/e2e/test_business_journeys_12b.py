"""Phase 12 — lot 12b : parcours métier de bout en bout.

    administrateur (API réelle : TLS → Nginx → backend, session + CSRF)
        → membres, abonnements, paiements, identités liées, protection
    agent RÉEL (faux Exit Node, HMAC, envoi direct) → observations en base
    réconciliation : commande de production `python -m app.cli reconcile` (horloge réelle)
                     puis service `reconcile()` à T0 + 24 h (horloge figée, temps simulé)
    navigateur réel (Chrome) → vue d'ensemble, identités, quarantaine, décisions, exemptions, audit

Quatre personnes, quatre parcours :
    A  actif@…     membre avec abonnement payé en cours    → Accès valide, aucun dossier
    B  expire@…    membre dont l'abonnement a expiré        → dossier PENDING, puis 24 h plus tard
                                                              EXPIRED + blocage SIMULÉ (dry_run, enforced=false)
    C  inconnu@…   identité sans membre                     → dossier PENDING, autorisé dans le navigateur
                                                              → exemption ; nouvelle activité : aucun dossier
    D  ops@…       identité protégée                        → Protégée, aucun dossier

Le temps n'est jamais simulé dans le code de production : la seconde réconciliation appelle le service
existant avec une horloge figée (FixedClock) et une tolérance de fraîcheur de l'agent couvrant le saut
de 24 h (paramètre `health_max_age` existant), puisque l'agent réel ne peut pas émettre dans le futur.
"""
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from agent_harness import Admin, Device, FakeExitNode, cli, create_agent, run_agent, summary
from conftest import BACKEND, SKIP, run_browser

pytestmark = SKIP

A = Device("100.70.0.11", login="Actif@Example.com", user_id=1101, stable_id="nA11", hostname="tel-a", flows=4)
B = Device("100.70.0.12", login="expire@example.com", user_id=1102, stable_id="nB12", hostname="tel-b", flows=4)
C = Device("100.70.0.13", login="inconnu@example.com", user_id=1103, stable_id="nC13", hostname="tel-c", flows=4)
D = Device("100.70.0.14", login="ops@example.com", user_id=1104, stable_id="nD14", hostname="tel-d", flows=4)
NODE, NODE_IP = "exit-e2e-12b", "100.85.214.12"
NOTE_C = "Invité autorisé (12b)"


def rows(stack, sql, **params):
    with stack["engine"].connect() as c:
        return c.execute(text(sql), params).all()


def scalar(stack, sql, **params):
    with stack["engine"].connect() as c:
        return c.execute(text(sql), params).scalar()


def records(stack):
    """Dossiers par login : [(statut), ...] dans l'ordre d'ouverture."""
    out = {}
    for login, st in rows(stack, "SELECT i.login_name, r.status FROM quarantine_records r "
                                 "JOIN exit_node_identities i ON i.id = r.exit_node_identity_id ORDER BY r.started_at"):
        out.setdefault(login, []).append(st)
    return out


def reconcile_cli(stack, *args) -> str:
    res = cli(stack, "reconcile", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def counter(out: str, name: str) -> int:
    return int(re.search(rf"{name} (\d+)", out).group(1))


@pytest.fixture(scope="module")
def world(stack):
    node = FakeExitNode()
    node.configure([A, B, C, D])
    admin = Admin(stack)
    plan = next(p for p in admin.get("/api/plans") if not p["is_unlimited"] and p["duration_months"] == 1)
    method = admin.get("/api/payment-methods")[0]["id"]
    today = date.today()

    def member(name, login, starts_on):
        m = admin.post("/api/members", {"full_name": name, "email": f"contact.{login}"})
        paid_at = datetime.combine(starts_on, datetime.min.time(), timezone.utc) + timedelta(hours=12)
        admin.post("/api/subscriptions", {"member_id": m["id"], "plan_id": plan["id"], "starts_on": starts_on.isoformat(),
                                          "payment": {"payment_method_id": method, "paid_at": paid_at.isoformat()}})
        admin.post(f"/api/members/{m['id']}/identities", {"login_name": login})
        return m

    member("Awa Active", A.login, today)
    member("Eli Expiré", B.login, today - timedelta(days=120))      # 1 mois + 3 j de grâce : expiré depuis longtemps
    admin.post("/api/protected-identities", {"login_name": D.login, "reason": "Compte d'exploitation"})
    agent = create_agent(stack, node, NODE, NODE_IP)
    yield {"node": node, "admin": admin, "agent": agent}
    node.close()


def observe(world, cycles=3):
    """Le vrai agent tourne quelques cycles : ≥ 2 fenêtres consécutives confirment l'activité."""
    res = run_agent(world["node"], world["agent"], "--cycles", str(cycles), "--interval", "1", "--window", "60",
                    "--quiet", "--spool", str(world["node"].base / "spool.db"))
    assert res.returncode == 0, res.stdout + res.stderr
    s = summary(res.stdout)
    assert s["envoyes"] == str(cycles) and s["refuses"] == "0" and s["erreurs"] == "0", s


def test_1_observation_and_verdicts(stack, world):
    observe(world)
    page = world["admin"].get("/api/identities")
    got = {i["login_name"]: (i["verdict"], i["active_now"]) for i in page["items"]}
    assert got == {
        "actif@example.com": ("OK", True),
        "expire@example.com": ("EXPIRED_CANDIDATE", True),
        "inconnu@example.com": ("UNKNOWN_IDENTITY", True),
        "ops@example.com": ("PROTECTED", True),
    }, got


def test_2_first_reconciliation_opens_only_unauthorized_identities(stack, world):
    plan = reconcile_cli(stack, "--plan")
    assert counter(plan, "ouvertes") == 2
    assert records(stack) == {}, "--plan n'écrit rien"
    out = reconcile_cli(stack)
    assert (counter(out, "ouvertes"), counter(out, "changées")) == (2, 0), out
    assert records(stack) == {"expire@example.com": ["PENDING"], "inconnu@example.com": ["PENDING"]}
    assert scalar(stack, "SELECT count(*) FROM enforcement_decisions") == 0, "aucune décision à l'ouverture"
    assert scalar(stack, "SELECT status FROM job_runs WHERE job_name = 'reconcile' ORDER BY id DESC LIMIT 1") == "SUCCESS"


def test_3_browser_sees_pending_cases_and_authorizes_the_unknown_identity(stack, world):
    run_browser(stack, "browser_12b.mjs", "avant", NOTE_C)
    rec = rows(stack, "SELECT r.status, r.resolution_note FROM quarantine_records r JOIN exit_node_identities i "
                      "ON i.id = r.exit_node_identity_id WHERE i.login_name = :l", l=C.login)
    assert rec == [("AUTHORIZED", NOTE_C)]
    ex = rows(stack, "SELECT r.resolution_note, x.ended_at, a.email FROM identity_exemptions x "
                     "JOIN quarantine_records r ON r.id = x.quarantine_record_id "
                     "JOIN exit_node_identities i ON i.id = x.exit_node_identity_id "
                     "JOIN admin_users a ON a.id = x.granted_by_admin_id WHERE i.login_name = :l", l=C.login)
    assert ex == [(NOTE_C, None, stack["email"])], "exemption créée par l'autorisation, au nom de l'administrateur connecté"


def test_4_new_activity_of_the_exempt_identity_opens_nothing(stack, world):
    observe(world)
    out = reconcile_cli(stack)
    assert counter(out, "ouvertes") == 0, out
    assert records(stack)["inconnu@example.com"] == ["AUTHORIZED"]
    verdicts = {i["login_name"]: i["verdict"] for i in world["admin"].get("/api/identities")["items"]}
    assert verdicts["inconnu@example.com"] == "UNKNOWN_IDENTITY", "l'exemption ne change pas le verdict, seulement le dossier"


def test_5_after_24_hours_the_expired_member_gets_a_simulated_block(stack, world):
    sys.path.insert(0, str(BACKEND))
    from app.db.session import get_sessionmaker
    from app.domain.clock import FixedClock
    from app.services import quarantine as svc

    started = scalar(stack, "SELECT r.started_at FROM quarantine_records r JOIN exit_node_identities i "
                            "ON i.id = r.exit_node_identity_id WHERE i.login_name = :l", l=B.login)
    early = FixedClock(started + timedelta(hours=23, minutes=59))
    later = FixedClock(started + timedelta(hours=24, minutes=1))
    with get_sessionmaker()() as db:
        report = svc.reconcile(db, early, health_max_age=timedelta(hours=25))
    assert (report.changed, report.blocked) == (0, 0), "rien avant la fin du délai de 24 h"
    with get_sessionmaker()() as db:
        report = svc.reconcile(db, later, health_max_age=timedelta(hours=25))
    assert (report.opened, report.changed, report.blocked, report.errors) == (0, 1, 1, 0), report.details()
    assert records(stack) == {"expire@example.com": ["EXPIRED"], "inconnu@example.com": ["AUTHORIZED"]}
    decisions = rows(stack, "SELECT i.login_name, d.decision, d.mode, d.enforced, d.reason FROM enforcement_decisions d "
                            "JOIN exit_node_identities i ON i.id = d.exit_node_identity_id")
    assert decisions == [("expire@example.com", "WOULD_BLOCK", "dry_run", False, "window_elapsed")]


def test_6_browser_shows_the_outcome_everywhere(stack, world):
    run_browser(stack, "browser_12b.mjs", "apres", NOTE_C)


def test_7_nothing_was_enforced(stack, world):
    assert scalar(stack, "SELECT count(*) FROM enforcement_decisions WHERE mode <> 'dry_run' OR enforced") == 0
    health = world["admin"].get("/api/health/details")
    assert health["mode"] == {**health["mode"], "revocation_mode": "dry_run", "enforcer_enabled": False}
    assert not any(k in json.dumps(health).lower() for k in ("secret", "key_id"))
    # A et D n'ont jamais eu de dossier ; aucune décision pour eux.
    assert set(records(stack)) == {"expire@example.com", "inconnu@example.com"}
