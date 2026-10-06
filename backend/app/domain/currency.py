"""Conversion EUR -> XOF à parité fixe (le XOF est arrimé à l'euro)."""
from decimal import ROUND_HALF_UP, Decimal

EUR_XOF_RATE = Decimal("655.957")


def eur_cents_to_xof(cents: int, rate: Decimal = EUR_XOF_RATE) -> int:
    """Convertit des centimes d'euro en francs CFA entiers (arrondi au franc, demi vers le haut)."""
    if cents < 0:
        raise ValueError("montant négatif")
    xof = (Decimal(cents) * rate / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(xof)


def settlement_for(
    currency: str, amount: int, settled_amount: int | None = None, rate: Decimal = EUR_XOF_RATE,
) -> tuple[int | None, str | None, Decimal | None]:
    """Champs de règlement d'un paiement : (settled_amount, settled_currency, fx_rate).

    XOF : aucun règlement (le paiement est déjà en XOF).
    EUR : règlement en XOF, saisi ou calculé à parité.
    """
    if currency == "XOF":
        if settled_amount is not None:
            raise ValueError("montant réglé inutile : le paiement est déjà en XOF")
        return None, None, None
    if currency == "EUR":
        if settled_amount is not None and settled_amount <= 0:
            raise ValueError("le montant réglé doit être positif")
        settled = settled_amount if settled_amount is not None else eur_cents_to_xof(amount, rate)
        return settled, "XOF", rate
    raise ValueError(f"devise non prise en charge : {currency}")
