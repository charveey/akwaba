"""Lot 11b — parcours navigateur réel : membres, abonnements, paiements, relances WhatsApp.

Pile complète (conftest.py) : Chrome → TLS → Nginx → backend → PostgreSQL. Horloge réelle du backend :
l'abonnement de la relance est daté pour que son échéance tombe dans la fenêtre J-7…J-1.
"""
import sys
from datetime import datetime, timedelta, timezone

from conftest import BACKEND, SKIP, run_browser

pytestmark = SKIP
sys.path.insert(0, str(BACKEND))


def reminder_start() -> tuple[str, int]:
    """starts_on d'un abonnement d'un mois dont l'échéance tombe aujourd'hui + n (n ∈ 3, 2, 1, 7)."""
    from app.domain.reminders import offsets_due_today
    from app.domain.subscriptions import add_months

    today = datetime.now(timezone.utc).date()
    for n in (3, 2, 1, 7):
        target = today + timedelta(days=n)
        for back in range(25, 35):
            start = target - timedelta(days=back)
            if add_months(start, 1) == target and start <= today:
                assert offsets_due_today(target, today) == [-n]
                return start.isoformat(), -n
    raise AssertionError("aucune date de début trouvée")


def test_lot_11b_browser_journey(stack):
    start, offset = reminder_start()
    run_browser(stack, "browser_11b.mjs", start, str(offset))


def test_payment_history_is_never_deleted(stack):
    """Après le parcours : le paiement annulé est toujours en base (VOIDED), avec son motif."""
    from sqlalchemy import text
    with stack["engine"].connect() as c:
        rows = c.execute(text("SELECT status, void_reason FROM payments ORDER BY paid_at")).all()
    assert ("VOIDED", "Paiement saisi en double") in [tuple(r) for r in rows]
    assert sum(1 for r in rows if r[0] == "CONFIRMED") == 2


def test_reminder_sent_only_after_explicit_confirmation(stack):
    from sqlalchemy import text
    with stack["engine"].connect() as c:
        actions = [r[0] for r in c.execute(text(
            "SELECT action FROM audit_logs WHERE action LIKE 'reminder.%' ORDER BY id")).all()]
    assert actions == ["reminder.opened", "reminder.sent"]


def test_archive_is_final(stack):
    from sqlalchemy import text
    with stack["engine"].connect() as c:
        archived = c.execute(text("SELECT count(*) FROM members WHERE archived_at IS NOT NULL")).scalar_one()
    assert archived == 1
