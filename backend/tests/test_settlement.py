from decimal import Decimal

import pytest

from app.domain.currency import EUR_XOF_RATE, settlement_for


def test_eur_defaults_to_parity():
    assert settlement_for("EUR", 499) == (3273, "XOF", EUR_XOF_RATE)
    assert settlement_for("EUR", 5399) == (35415, "XOF", Decimal("655.957"))


def test_eur_with_custom_settled_amount_keeps_parity_rate():
    assert settlement_for("EUR", 499, 3200) == (3200, "XOF", EUR_XOF_RATE)


def test_xof_has_no_settlement():
    assert settlement_for("XOF", 3273) == (None, None, None)


def test_xof_with_settled_amount_is_rejected():
    with pytest.raises(ValueError):
        settlement_for("XOF", 3273, 3000)


def test_unsupported_currency_and_non_positive_settlement_are_rejected():
    with pytest.raises(ValueError):
        settlement_for("USD", 500)
    with pytest.raises(ValueError):
        settlement_for("EUR", 499, 0)
