"""Cas limites demandés : dates fixes, horloge contrôlée, aucun appel à l'horloge système."""
import inspect
from datetime import date, datetime, timezone

import pytest

from app.domain import subscriptions
from app.domain.clock import FixedClock, SystemClock
from app.domain.subscriptions import (
    AccessStatus as A, RenewalStatus as R, access_status, add_months, compute_expires_on,
    renewal_start, renewal_status,
)


def clock(y, m, d) -> FixedClock:
    return FixedClock(datetime(y, m, d, 12, 0, tzinfo=timezone.utc))


def test_business_logic_never_reads_the_system_clock():
    source = inspect.getsource(subscriptions)
    for forbidden in ("date.today", "datetime.now", "datetime.utcnow", "time.time"):
        assert forbidden not in source


def test_fixed_clock_is_frozen_and_requires_timezone():
    c = clock(2026, 1, 17)
    assert c.today() == date(2026, 1, 17) == c.today()
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 1, 17))


def test_system_clock_returns_utc_date():
    assert SystemClock().now().tzinfo is not None


def test_month_end_clamping_is_deterministic():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_renewal_after_a_clamped_date_drifts_to_the_28th():
    first = compute_expires_on(date(2026, 1, 31), 1)
    assert first == date(2026, 2, 28)
    second = compute_expires_on(renewal_start(first, date(2026, 2, 28), 3), 1)
    assert second == date(2026, 3, 28)
    third = compute_expires_on(renewal_start(second, date(2026, 3, 28), 3), 1)
    assert third == date(2026, 4, 28)


EXP = date(2026, 1, 17)


@pytest.mark.parametrize("payment, expected_start", [
    (date(2026, 1, 16), date(2026, 1, 17)),
    (date(2026, 1, 17), date(2026, 1, 17)),
    (date(2026, 1, 19), date(2026, 1, 17)),
    (date(2026, 1, 20), date(2026, 1, 20)),
])
def test_payment_boundaries_around_expiry_and_grace(payment, expected_start):
    assert renewal_start(EXP, payment, 3) == expected_start


def test_subscription_not_started_yet_has_no_access():
    s = access_status(today=date(2026, 1, 10), starts_on=date(2026, 1, 17), expires_on=date(2026, 2, 17),
                      is_unlimited=False, grace_period_days=3, payment_status="PAID", canceled=False, suspended=False)
    assert s == A.NONE


def test_statuses_follow_the_fixed_clock_through_a_full_lifecycle():
    kw = dict(starts_on=date(2026, 1, 17), expires_on=date(2026, 2, 17), is_unlimited=False,
              grace_period_days=3, payment_status="PAID", canceled=False, suspended=False)
    rk = dict(expires_on=date(2026, 2, 17), is_unlimited=False, grace_period_days=3, canceled=False, has_successor=False)
    timeline = [
        ((2026, 2, 16), A.ACTIVE, R.UP_TO_DATE),
        ((2026, 2, 17), A.GRACE_PERIOD, R.RENEWAL_OVERDUE),
        ((2026, 2, 19), A.GRACE_PERIOD, R.RENEWAL_OVERDUE),
        ((2026, 2, 20), A.EXPIRED, R.NON_RENEWAL),
    ]
    for ymd, access, renewal in timeline:
        today = clock(*ymd).today()
        assert access_status(today=today, **kw) == access
        assert renewal_status(today=today, **rk) == renewal


def test_chain_of_renewals_keeps_continuity():
    expires = compute_expires_on(date(2026, 1, 17), 1)
    for pay in (date(2026, 2, 10), date(2026, 3, 19), date(2026, 4, 17)):
        start = renewal_start(expires, pay, 3)
        assert start == expires
        expires = compute_expires_on(start, 1)
    assert expires == date(2026, 5, 17)
