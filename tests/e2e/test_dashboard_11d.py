"""Lot 11d — parcours navigateur réel : vue d'ensemble, agent, dépenses, rapports (CSV), journal d'audit.
Données injectées par l'ingestion réelle (agent) et l'ORM (membres) ; dépenses saisies dans l'interface."""
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

from conftest import BACKEND, SKIP, run_browser

pytestmark = SKIP
sys.path.insert(0, str(BACKEND))
STATE = {}


def seed(stack):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from app.domain.clock import FixedClock
    from app.models.billing import Payment, PaymentMethod, Subscription, SubscriptionPlan
    from app.models.clients import Member
    from app.schemas.agent import BatchIn
    from app.services import agent_admin
    from app.services.agent_auth import AgentContext
    from app.services.ingest import ingest_batch

    engine = stack["engine"]
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with maker() as db:
        key_id, secret = agent_admin.create_agent(db, name="stray-11d", ip="100.85.214.5", rotate=False, now=now)
    STATE.update(key_id=key_id, secret=secret, today=now.date().isoformat())
    with engine.connect() as c:
        node = c.execute(text("SELECT id FROM exit_nodes WHERE name = 'stray-11d'")).scalar_one()
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO agent_heartbeats (exit_node_id, received_at, agent_version, protocol_version, conntrack_ok, "
            "status_ok, whois_ok, bytes_available, spool_depth, conntrack_count, conntrack_max, details) VALUES "
            "(:n, :t, '1.4.0', 1, true, true, true, true, 2, 420, 1000, CAST(:d AS jsonb))"),
            {"n": node, "t": now, "d": '{"generated_at": "%s", "clock_skew_seconds": 1, "conntrack_saturation": 0.42, '
             '"spool_oldest_age_seconds": 12, "send_failures": 0, "rejected_batches": 0, "dropped_batches": 0, '
             '"backend_reachable": true}' % (now - timedelta(seconds=2)).isoformat()})
    batch = BatchIn.model_validate({
        "batch_id": str(uuid.uuid4()), "schema": 1,
        "sampled_at": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
        "window_seconds": 60, "conntrack_ok": True, "status_ok": True, "whois_ok": True,
        "observations": [{
            "src_ip": "100.70.1.2", "total_flows": 10, "recent_flows": 8, "recent_replied_flows": 6,
            "unknown_age_flows": 1, "new_flows": 2, "active_window": True, "consecutive_active_windows": 2,
            "active_confirmed": True, "identity_status": "RESOLVED", "identity_technical_error": False,
            "identity": {"login_name": "observe@example.com", "tailscale_user_id": random.randint(1, 999),
                         "stable_id": "n" + uuid.uuid4().hex[:10], "is_sharee": False}, "peer": None}]})
    with maker() as db:
        ingest_batch(db, AgentContext(exit_node_id=node, exit_node_name="stray-11d", key_id=key_id, body=b"",
                                      now=now.timestamp()), FixedClock(now), batch)
    with maker() as db:
        method = {m.code: m for m in db.query(PaymentMethod).all()}
        for name, plan, pay in [("Ama Active", "M1", "WAVE"), ("Bob Actif", "M3", "CASH"), ("Cissé Attente", "M6", None)]:
            m = Member(full_name=name)
            db.add(m)
            db.flush()
            p = db.query(SubscriptionPlan).filter_by(code=plan).one()
            s = Subscription(member_id=m.id, plan_id=p.id, starts_on=now.date(),
                             expires_on=now.date() + timedelta(days=30 * p.duration_months), grace_period_days=3,
                             price_amount=p.price_amount, currency=p.currency,
                             payment_status="PAID" if pay else "PENDING")
            db.add(s)
            db.flush()
            if pay:
                db.add(Payment(subscription_id=s.id, payment_method_id=method[pay].id, amount=p.price_amount,
                               currency=p.currency, settled_amount=round(p.price_amount * 6.55957),
                               settled_currency="XOF", fx_rate=655.957, paid_at=now))
        db.commit()


def test_lot_11d_browser_journey(stack):
    seed(stack)
    run_browser(stack, "browser_11d.mjs", STATE["today"], STATE["key_id"], STATE["secret"])


def test_correction_is_atomic_in_database_and_trigger_still_active(stack):
    from sqlalchemy import text
    with stack["engine"].connect() as c:
        voided = c.execute(text("SELECT void_reason FROM expenses WHERE status = 'VOIDED' ORDER BY created_at")).scalars().all()
        corrected = c.execute(text("SELECT details FROM audit_logs WHERE action = 'expense.corrected'")).scalars().all()
        trigger = c.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname = 'expenses_guard' AND tgenabled <> 'D'")).scalar_one()
    assert "Corrigée" in voided and len(corrected) == 1 and trigger == 1
