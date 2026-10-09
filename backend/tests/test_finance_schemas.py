import pytest
from pydantic import ValidationError

from app.schemas.finance import CategoryIn, ExpenseCreate

CAT = "00000000-0000-0000-0000-000000000001"


def test_category_name_is_normalized():
    assert CategoryIn(name="  Frais   de  paiement ").name == "Frais de paiement"


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_invalid_category_names(name):
    with pytest.raises(ValidationError):
        CategoryIn(name=name)


@pytest.mark.parametrize("amount", [0, -5, 10.5, "100", True, 1_000_000_001])
def test_amount_must_be_a_positive_strict_integer(amount):
    with pytest.raises(ValidationError):
        ExpenseCreate(category_id=CAT, amount=amount, expense_date="2026-03-10")


def test_currency_cannot_be_injected_and_blank_description_is_none():
    with pytest.raises(ValidationError):
        ExpenseCreate(category_id=CAT, amount=100, expense_date="2026-03-10", currency="EUR")
    e = ExpenseCreate(category_id=CAT, amount=100, expense_date="2026-03-10", description="   ")
    assert e.description is None
