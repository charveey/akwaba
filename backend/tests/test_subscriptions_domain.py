from datetime import date

import pytest

from app.core.config import Settings
from app.domain.subscriptions import (
    AccessStatus as A, RenewalStatus as R, access_status, add_months, compute_expires_on,
    display_end, grace_end, renewal_start, renewal_status,
)


@pytest.mark.parametrize("start, months, expected", [
    (date(2026, 12, 17), 1, date(2027, 1, 17)),
    (date(2026, 1, 17), 3, date(2026, 4, 17)),
    (date(2026, 1, 17), 12, date(2027, 1, 17)),
    (date(2026, 1, 31), 1, date(2026, 2, 28)),
    (date(2028, 1, 31), 1, date(2028, 2, 29)),
    (date(2026, 3, 31), 1, date(2026, 4, 30)),
    (date(2028, 2, 29), 12, date(2029, 2, 28)),
    (date(2026, 8, 31), 6, date(2027, 2, 28)),
])
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


def test_add_months_rejects_negative():
    with pytest.raises(ValueError):
        add_months(date(2026, 1, 1), -1)


def test_expiry_and_display_end():
    assert compute_expires_on(date(2026, 12, 17), 1) == date(2027, 1, 17)
    assert display_end(date(2027, 1, 17)) == date(2027, 1, 16)
    assert compute_expires_on(date(2026, 1, 1), None) is None
    assert display_end(None) is None
    with pytest.raises(ValueError):
        compute_expires_on(date(2026, 1, 1), 0)


def test_grace_end_is_exclusive():
    assert grace_end(date(2026, 1, 17), 3) == date(2026, 1, 20)


EXP = date(2026, 1, 17)


def test_renewal_payment_in_grace_starts_at_previous_expiry():
    assert renewal_start(EXP, date(2026, 1, 19), 3) == date(2026, 1, 17)


def test_renewal_payment_on_first_and_last_grace_day():
    assert renewal_start(EXP, date(2026, 1, 17), 3) == date(2026, 1, 17)
    assert renewal_start(EXP, date(2026, 1, 19), 3) == date(2026, 1, 17)


def test_renewal_payment_after_grace_starts_at_payment_date():
    assert renewal_start(EXP, date(2026, 1, 20), 3) == date(2026, 1, 20)
    assert renewal_start(EXP, date(2026, 1, 25), 3) == date(2026, 1, 25)


def test_renewal_early_payment_keeps_continuity():
    assert renewal_start(EXP, date(2026, 1, 10), 3) == date(2026, 1, 17)


def test_renewal_payment_date_rule_in_grace():
    assert renewal_start(EXP, date(2026, 1, 19), 3, "PAYMENT_DATE") == date(2026, 1, 19)
    assert renewal_start(EXP, date(2026, 1, 10), 3, "PAYMENT_DATE") == date(2026, 1, 17)


def test_spec_examples_end_to_end():
    start = renewal_start(EXP, date(2026, 1, 19), 3)
    assert (start, compute_expires_on(start, 1)) == (date(2026, 1, 17), date(2026, 2, 17))
    start = renewal_start(EXP, date(2026, 1, 25), 3)
    assert (start, compute_expires_on(start, 1)) == (date(2026, 1, 25), date(2026, 2, 25))


def test_default_rule_in_settings_is_rule_a():
    s = Settings(database_url="postgresql+psycopg://u:p@h/d", secret_key="s" * 40, field_encryption_key="A" * 43 + "=")
    assert s.renewal_in_grace_start == "PREVIOUS_EXPIRY"


def acc(today, **kw):
    base = dict(starts_on=date(2026, 1, 17), expires_on=date(2026, 2, 17), is_unlimited=False,
                grace_period_days=3, payment_status="PAID", canceled=False, suspended=False)
    return access_status(today=today, **{**base, **kw})


def test_access_timeline():
    assert acc(date(2026, 1, 16)) == A.NONE
    assert acc(date(2026, 1, 17)) == A.ACTIVE
    assert acc(date(2026, 2, 16)) == A.ACTIVE
    assert acc(date(2026, 2, 17)) == A.GRACE_PERIOD
    assert acc(date(2026, 2, 19)) == A.GRACE_PERIOD
    assert acc(date(2026, 2, 20)) == A.EXPIRED


def test_access_pending_canceled_suspended():
    assert acc(date(2026, 1, 20), payment_status="PENDING") == A.NONE
    assert acc(date(2026, 1, 20), canceled=True) == A.EXPIRED
    assert acc(date(2026, 1, 20), suspended=True) == A.SUSPENDED
    assert acc(date(2026, 1, 20), suspended=True, canceled=True) == A.SUSPENDED


def test_platinum_is_always_active_without_grace():
    for today in (date(2026, 1, 17), date(2040, 6, 1)):
        assert acc(today, is_unlimited=True, expires_on=None, payment_status="WAIVED") == A.ACTIVE


def ren(today, **kw):
    base = dict(expires_on=date(2026, 2, 17), is_unlimited=False, grace_period_days=3,
                canceled=False, has_successor=False)
    return renewal_status(today=today, **{**base, **kw})


def test_renewal_status_timeline():
    assert ren(date(2026, 2, 16)) == R.UP_TO_DATE
    assert ren(date(2026, 2, 17)) == R.RENEWAL_OVERDUE
    assert ren(date(2026, 2, 19)) == R.RENEWAL_OVERDUE
    assert ren(date(2026, 2, 20)) == R.NON_RENEWAL


def test_renewal_status_special_cases():
    assert ren(date(2026, 3, 1), has_successor=True) == R.UP_TO_DATE
    assert ren(date(2026, 2, 1), canceled=True) == R.NON_RENEWAL
    assert ren(date(2030, 1, 1), is_unlimited=True, expires_on=None) == R.NOT_APPLICABLE
