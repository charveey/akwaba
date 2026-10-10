"""Règles pures des relances : calendrier, éligibilité, texte, lien wa.me. Aucune horloge implicite."""
from datetime import date, timedelta
from urllib.parse import quote

OFFSETS = (-7, -3, -2, -1, 0, 1)

_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre")


def french_date(d: date, *, with_year: bool = True) -> str:
    day = "1er" if d.day == 1 else str(d.day)
    text = f"{day} {_MONTHS[d.month - 1]}"
    return f"{text} {d.year}" if with_year else text


def due_date(expires_on: date, offset_days: int) -> date:
    return expires_on + timedelta(days=offset_days)


def offsets_due_today(expires_on: date, today: date) -> list[int]:
    return [o for o in OFFSETS if due_date(expires_on, o) == today]


def is_eligible(
    *, is_unlimited: bool, payment_status: str, canceled: bool, suspended: bool,
    has_successor: bool, member_archived: bool, phone_e164: str | None,
) -> bool:
    return not (
        is_unlimited or payment_status != "PAID" or canceled or suspended
        or has_successor or member_archived or not phone_e164
    )


def message_text(offset_days: int, duration_months: int, expires_on: date, today: date) -> str:
    """Deux familles de message : avant l'expiration (J-7 à J0) et après (J+1)."""
    if duration_months is None or duration_months <= 0:
        raise ValueError("duration_months doit être > 0 (les plans illimités ne sont pas relancés)")
    plan = f"{duration_months} mois"
    if offset_days <= 0:
        if offset_days == 0:
            when = "aujourd'hui"
        else:
            days = -offset_days
            when = f"dans {days} {'jour' if days == 1 else 'jours'}"
        return (
            f"Bonjour,\n\nVotre abonnement de {plan} à Akwaba VPN expire {when}. "
            "Souhaitez-vous reconduire l'abonnement ?\n\n"
            "Merci et excellente journée à vous."
        )
    since = french_date(expires_on, with_year=expires_on.year != today.year)
    return (
        f"Bonjour,\n\nVotre abonnement de {plan} à Akwaba VPN a expiré depuis le {since}. "
        "Votre compte sera bientôt désactivé si vous ne reconduisez pas votre abonnement.\n\n"
        "Merci et bonne journée à vous."
    )


def whatsapp_link(phone_e164: str, text: str) -> str:
    return f"https://wa.me/{phone_e164.lstrip('+')}?text={quote(text, safe='')}"
