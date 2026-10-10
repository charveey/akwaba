"""Règles pures des relances : calendrier, éligibilité, texte, lien wa.me. Aucune horloge implicite."""
from datetime import date, timedelta
from urllib.parse import quote

OFFSETS = (-7, -3, -2, -1, 0, 1)

_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre")


def french_date(d: date) -> str:
    day = "1er" if d.day == 1 else str(d.day)
    return f"{day} {_MONTHS[d.month - 1]} {d.year}"


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


def message_text(offset_days: int, first_name: str, expires_on: date) -> str:
    when = french_date(expires_on)
    hello = f"Bonjour {first_name}," if first_name else "Bonjour,"
    if offset_days < 0:
        days = -offset_days
        unit = "jour" if days == 1 else "jours"
        return f"{hello} votre abonnement expire le {when} (dans {days} {unit}). Pensez à le renouveler."
    if offset_days == 0:
        return f"{hello} votre abonnement expire aujourd'hui, le {when}. Pensez à le renouveler."
    return f"{hello} votre abonnement a expiré le {when}. Contactez-nous pour le renouveler."


def whatsapp_link(phone_e164: str, text: str) -> str:
    return f"https://wa.me/{phone_e164.lstrip('+')}?text={quote(text, safe='')}"


def first_name_of(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else ""
