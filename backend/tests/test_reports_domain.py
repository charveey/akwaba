from datetime import date

import pytest

from app.domain.reports import merge_profit, period_label


@pytest.mark.parametrize("unit, start, label", [
    ("month", date(2026, 3, 1), "2026-03"),
    ("month", date(2026, 12, 1), "2026-12"),
    ("quarter", date(2026, 1, 1), "2026-Q1"),
    ("quarter", date(2026, 4, 1), "2026-Q2"),
    ("quarter", date(2026, 10, 1), "2026-Q4"),
    ("year", date(2026, 1, 1), "2026"),
])
def test_period_labels(unit, start, label):
    assert period_label(unit, start) == label


def test_unknown_unit_is_rejected():
    with pytest.raises(ValueError):
        period_label("week", date(2026, 1, 1))


def test_merge_profit_covers_periods_from_either_side_and_allows_loss():
    jan, feb, mar = date(2038, 1, 1), date(2038, 2, 1), date(2038, 3, 1)
    rows = merge_profit({jan: 3273, feb: 8521}, {feb: 1000, mar: 500})
    assert rows == [(jan, 3273, 0, 3273), (feb, 8521, 1000, 7521), (mar, 0, 500, -500)]


def test_merge_profit_empty():
    assert merge_profit({}, {}) == []
