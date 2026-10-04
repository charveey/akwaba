"""Conversion EUR -> XOF à parité fixe (le XOF est arrimé à l'euro)."""
from decimal import ROUND_HALF_UP, Decimal

EUR_XOF_RATE = Decimal("655.957")


def eur_cents_to_xof(cents: int, rate: Decimal = EUR_XOF_RATE) -> int:
    """Convertit des centimes d'euro en francs CFA entiers (arrondi au franc, demi vers le haut)."""
    if cents < 0:
        raise ValueError("montant négatif")
    xof = (Decimal(cents) * rate / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(xof)
