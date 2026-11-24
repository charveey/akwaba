"""Phase 11b : statut métier d'un abonnement (fonction pure) et attribut « changement de niveau »."""
from datetime import date

import pytest

from app.domain.subscriptions import PaymentKind, payment_kind, plan_changed

EXP = date(2026, 2, 17)  # expires_on (exclusif) de l'abonnement précédent


def kind(**kw):
    base = dict(payment_status="PAID", has_predecessor=True, predecessor_expires_on=EXP, paid_on=date(2026, 2, 10))
    return payment_kind(**{**base, **kw})


def test_pending_wins_over_everything():
    assert kind(payment_status="PENDING") is PaymentKind.PENDING
    assert kind(payment_status="PENDING", has_predecessor=False) is PaymentKind.PENDING
    assert kind(payment_status="PENDING", paid_on=date(2026, 3, 1)) is PaymentKind.PENDING


def test_first_paid_subscription_is_new_member():
    assert kind(has_predecessor=False, predecessor_expires_on=None) is PaymentKind.NEW_MEMBER
    # Platinum offert (aucun paiement) : nouveau membre aussi.
    assert kind(payment_status="WAIVED", has_predecessor=False, paid_on=None) is PaymentKind.NEW_MEMBER


@pytest.mark.parametrize("paid_on, expected", [
    (date(2026, 1, 20), PaymentKind.RENEWED),        # anticipé
    (date(2026, 2, 16), PaymentKind.RENEWED),        # dernier jour d'accès payé
    (date(2026, 2, 17), PaymentKind.LATE_RENEWAL),   # le jour même de expires_on (exclusif) : en retard
    (date(2026, 2, 19), PaymentKind.LATE_RENEWAL),   # pendant la grâce
    (date(2026, 3, 30), PaymentKind.LATE_RENEWAL),   # après la grâce
])
def test_late_boundary_is_expires_on(paid_on, expected):
    assert kind(paid_on=paid_on) is expected


def test_unlimited_predecessor_never_late():
    assert kind(predecessor_expires_on=None, paid_on=date(2030, 1, 1)) is PaymentKind.RENEWED


def test_waived_renewal_is_renewed():
    assert kind(payment_status="WAIVED", paid_on=None) is PaymentKind.RENEWED


def test_plan_changed_is_independent_attribute():
    assert plan_changed("M1", "M3") is True
    assert plan_changed("M1", "M1") is False
    assert plan_changed(None, "M1") is False      # pas de prédécesseur : pas de changement
