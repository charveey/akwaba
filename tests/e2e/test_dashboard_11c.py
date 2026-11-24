"""Lot 11c — parcours navigateur réel : identités, association, réaffectation, quarantaine,
décisions simulées, exemptions, protections. Données injectées par l'ingestion et la réconciliation
RÉELLES du backend (horloge contrôlée pour les dossiers anciens). Aucune action réseau.
"""
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

from conftest import BACKEND, SKIP, run_browser

pytestmark = SKIP
sys.path.insert(0, str(BACKEND))


def _obs(login, ip=None, resolved=True):
    o = {
        "src_ip": ip or f"100.{random.randint(64, 127)}.{random.randint(0, 255)}.{random.randint(1, 254)}",
        "total_flows": 10, "recent_flows": 8, "recent_replied_flows": 6, "unknown_age_flows": 1, "new_flows": 2,
        "active_window": True, "consecutive_active_windows": 2, "active_confirmed": True,
        "identity_status": "RESOLVED", "identity_technical_error": False,
        "identity": {"login_name": login, "tailscale_user_id": random.randint(1000, 9999),
                     "stable_id": "n" + uuid.uuid4().hex[:10], "is_sharee": False},
        "peer": None,
    }
    if not resolved:
        o.update({"identity_status": "UNRESOLVED", "identity": None})
    return o


def seed(stack):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from app.domain.clock import FixedClock
    from app.schemas.agent import BatchIn
    from app.services import agent_admin
    from app.services import quarantine as qsvc
    from app.services.agent_auth import AgentContext
    from app.services.ingest import ingest_batch

    engine = stack["engine"]
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    past = now - timedelta(hours=25)
    with maker() as db:
        agent_admin.create_agent(db, name="stray-e2e", ip="100.85.214.5", rotate=False, now=past)
    with engine.connect() as c:
        node = c.execute(text("SELECT id FROM exit_nodes WHERE name = 'stray-e2e'")).scalar_one()

    def ingest(at, observations):
        batch = BatchIn.model_validate({
            "batch_id": str(uuid.uuid4()), "schema": 1,
            "sampled_at": (at - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
            "window_seconds": 60, "conntrack_ok": True, "status_ok": True, "whois_ok": True,
            "observations": observations})
        ctx = AgentContext(exit_node_id=node, exit_node_name="stray-e2e", key_id="agt_e2e", body=b"", now=at.timestamp())
        with maker() as db:
            ingest_batch(db, ctx, FixedClock(at), batch)

    def heartbeat(at):
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO agent_heartbeats (exit_node_id, received_at, agent_version, protocol_version, conntrack_ok, "
                "status_ok, whois_ok, bytes_available, spool_depth) VALUES (:n, :t, 'e2e', 1, true, true, true, false, 0)"),
                {"n": node, "t": at})

    def reconcile(at):
        with maker() as db:
            qsvc.reconcile(db, FixedClock(at))

    def member(name, email=None, login=None, plan=None):
        from app.models.billing import PaymentMethod, Subscription, SubscriptionPlan, Payment
        from app.models.clients import ClientIdentity, Member
        with maker() as db:
            m = Member(full_name=name, email=email)
            db.add(m)
            db.flush()
            if login:
                db.add(ClientIdentity(member_id=m.id, login_name=login))
            if plan:
                p = db.query(SubscriptionPlan).filter_by(code=plan).one()
                s = Subscription(member_id=m.id, plan_id=p.id, starts_on=now.date(),
                                 expires_on=now.date() + timedelta(days=30), grace_period_days=3,
                                 price_amount=p.price_amount, currency=p.currency, payment_status="PAID")
                db.add(s)
                db.flush()
                method = db.query(PaymentMethod).filter_by(code="CASH").one()
                db.add(Payment(subscription_id=s.id, payment_method_id=method.id, amount=p.price_amount,
                               currency=p.currency, settled_amount=3273, settled_currency="XOF", fx_rate=655.957,
                               paid_at=now))
            db.commit()
            return m.id

    # Dossier ancien → EXPIRED (blocage simulé) au passage actuel.
    heartbeat(past)
    ingest(past, [_obs("expire@example.com")])
    reconcile(past)
    # Identités actuelles.
    member("Revue Sans Abonnement", login="revue@example.com")
    member("Contact Suggestion", email="suggestion@example.com")
    member("Awa Active", login="awa@example.com", plan="M1")
    member("Binta Cible")
    heartbeat(now)
    ingest(now, [_obs(lg) for lg in ("inconnu@example.com", "revue@example.com", "ecarte@example.com",
                                      "suggestion@example.com", "awa@example.com")]
           + [_obs("x", ip="100.99.9.9", resolved=False)])
    reconcile(now)


def test_lot_11c_browser_journey(stack):
    seed(stack)
    run_browser(stack, "browser_11c.mjs")


def test_everything_stays_simulated_and_history_is_kept(stack):
    from sqlalchemy import text
    with stack["engine"].connect() as c:
        decisions = c.execute(text("SELECT decision, mode, enforced FROM enforcement_decisions ORDER BY id")).all()
        reassigned = c.execute(text("SELECT details FROM audit_logs WHERE action = 'identity.reassigned'")).scalars().all()
        links = c.execute(text("SELECT is_active FROM client_identities WHERE login_name = 'awa@example.com' ORDER BY linked_at")).scalars().all()
        exemptions = c.execute(text("SELECT ended_at IS NOT NULL FROM identity_exemptions")).scalars().all()
        unresolved_ids = c.execute(text("SELECT count(identity_id) FROM exit_node_observations WHERE tailscale_ip = '100.99.9.9'")).scalar_one()
    assert decisions and all(d[1] == "dry_run" and d[2] is False for d in decisions)
    assert [d[0] for d in decisions][:1] == ["WOULD_BLOCK"]
    assert len(reassigned) == 1 and reassigned[0]["login_name"] == "awa@example.com"
    assert links == [False, True]                      # ancienne association conservée, inactive
    assert exemptions == [True]                        # terminée, jamais supprimée
    assert unresolved_ids == 0
