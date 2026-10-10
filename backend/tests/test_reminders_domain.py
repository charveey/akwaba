from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app.domain.reminders import (
    OFFSETS, due_date, french_date, is_eligible, message_text, offsets_due_today, whatsapp_link,
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
    assert french_date(date(2026, 2, 17), with_year=False) == "17 février"


def test_before_expiry_message_matches_the_template():
    assert message_text(-2, 1, EXP, date(2026, 2, 15)) == (
        "Bonjour,\n\nVotre abonnement de 1 mois à Akwaba VPN expire dans 2 jours. "
        "Souhaitez-vous reconduire l'abonnement ?\n\nMerci et excellente journée à vous.")


def test_singular_plural_and_day_zero():
    assert "expire dans 1 jour. " in message_text(-1, 3, EXP, date(2026, 2, 16))
    assert "expire dans 7 jours. " in message_text(-7, 12, EXP, date(2026, 2, 10))
    assert "de 12 mois à Akwaba VPN" in message_text(-7, 12, EXP, date(2026, 2, 10))
    assert "expire aujourd'hui. " in message_text(0, 1, EXP, date(2026, 2, 17))


def test_after_expiry_message_matches_the_template_and_omits_current_year():
    assert message_text(1, 3, EXP, date(2026, 2, 18)) == (
        "Bonjour,\n\nVotre abonnement de 3 mois à Akwaba VPN a expiré depuis le 17 février. "
        "Votre compte sera bientôt désactivé si vous ne reconduisez pas votre abonnement.\n\n"
        "Merci et bonne journée à vous.")


def test_after_expiry_message_keeps_the_year_when_it_differs():
    text = message_text(1, 1, date(2025, 12, 30), date(2026, 1, 2))
    assert "a expiré depuis le 30 décembre 2025." in text


def test_message_requires_a_positive_duration():
    for bad in (None, 0, -1):
        with pytest.raises(ValueError):
            message_text(-7, bad, EXP, date(2026, 2, 10))


def test_whatsapp_link_has_no_plus_and_encodes_text():
    url = whatsapp_link("+2250102030405", "Bonjour,\n\nça va ? 100% & plus")
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "wa.me", "/2250102030405")
    assert parse_qs(parsed.query)["text"] == ["Bonjour,\n\nça va ? 100% & plus"]
    assert "+" not in parsed.path and " " not in url and "\n" not in url
