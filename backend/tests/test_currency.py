from decimal import Decimal

import pytest

from app.domain.currency import eur_cents_to_xof


@pytest.mark.parametrize("cents, xof", [(499, 3273), (1299, 8521), (2599, 17048), (5399, 35415)])
def test_plan_prices(cents, xof):
    assert eur_cents_to_xof(cents) == xof


def test_rounding_is_half_up():
    assert eur_cents_to_xof(1, Decimal("50")) == 1  # 0,5 -> 1


def test_negative_amount_is_rejected():
    with pytest.raises(ValueError):
        eur_cents_to_xof(-1)
