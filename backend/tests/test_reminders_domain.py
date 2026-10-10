from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app.domain.reminders import (
    OFFSETS, due_date, first_name_of, french_date, is_eligible, message_text, offsets_due_today, whatsapp_link,
)

EXP = date(2026, 2, 17)


def test_offsets_and_due_dates():
    assert OFFSETS == (-7, -3, -2, -1, 0, 1)
    assert due_date(EXP, -7) == date(2026, 2, 10)
    assert due_date(EXP, 0) == EXP
    assert due_date(EXP, 1) == date(2026, 2, 18)


@pytest.mark.parametrize("today, expected", [
    (date(2026, 2, 10), [-7]), (date(2026, 2, 14), [-3]), (date(2026, 2, 15), [-2]),
    (date(2026, 2, 16), [-1]), (date(2026, 2, 17), [0]), (date(2026, 2, 18), [1]),
    (date(2026, 2, 11), []), (date(2026, 2, 19), []),
])
def test_offsets_due_today(today, expected):
    assert offsets_due_today(EXP, today) == expected


def test_offsets_cross_month_and_year_boundaries():
    assert due_date(date(2026, 1, 3), -7) == date(2025, 12, 27)
    assert due_date(date(2026, 3, 1), -1) == date(2026, 2, 28)


def elig(**over):
    base = dict(is_unlimited=False, payment_status="PAID", canceled=False, suspended=False,
                has_successor=False, member_archived=False, phone_e164="+2250102030405")
    return is_eligible(**{**base, **over})


def test_eligibility():
    assert elig()
    for over in ({"is_unlimited": True}, {"payment_status": "PENDING"}, {"payment_status": "WAIVED"},
                 {"canceled": True}, {"suspended": True}, {"has_successor": True},
                 {"member_archived": True}, {"phone_e164": None}, {"phone_e164": ""}):
        assert not elig(**over), over


def test_french_date():
    assert french_date(date(2026, 2, 17)) == "17 février 2026"
    assert french_date(date(2026, 8, 1)) == "1er août 2026"
    assert french_date(date(2026, 12, 25)) == "25 décembre 2026"


def test_message_uses_expires_on_and_tone_by_offset():
    assert message_text(-7, "Awa", EXP) == (
        "Bonjour Awa, votre abonnement expire le 17 février 2026 (dans 7 jours). Pensez à le renouveler.")
    assert "dans 1 jour)" in message_text(-1, "Awa", EXP)
    assert "expire aujourd'hui, le 17 février 2026" in message_text(0, "Awa", EXP)
    assert message_text(1, "Awa", EXP) == (
        "Bonjour Awa, votre abonnement a expiré le 17 février 2026. Contactez-nous pour le renouveler.")
    assert message_text(0, "", EXP).startswith("Bonjour, ")


def test_first_name():
    assert first_name_of("  Awa  Koné ") == "Awa"
    assert first_name_of("   ") == ""


def test_whatsapp_link_has_no_plus_and_encodes_text():
    url = whatsapp_link("+2250102030405", "Bonjour Awa, ça va ? 100% & plus")
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "wa.me", "/2250102030405")
    assert parse_qs(parsed.query)["text"] == ["Bonjour Awa, ça va ? 100% & plus"]
    assert "+" not in parsed.path and " " not in url
