"""Lot 11d — unitaires : export CSV (injection de formules) et masquage récursif de l'audit."""
import pytest

from app.core.csv_export import BOM, csv_response, neutralize, render
from app.services.reports import MASK, actor_of, mask


@pytest.mark.parametrize("raw", [
    "=SOMME(A1:A2)", "+33 6 00", "-2+3", "@cmd", " =1+1", "\t=1+1", "\r=1", "\n=1", "\x00=1", "\x1f@x", "  \t -1",
])
def test_formulas_are_neutralized_even_after_spaces_or_control_chars(raw):
    assert neutralize(raw) == "'" + raw


@pytest.mark.parametrize("raw", ["Hébergement", "Dépense = loyer", "a-b", "100.64.0.7", ""])
def test_plain_text_is_kept(raw):
    assert neutralize(raw) == raw


def test_numbers_stay_numeric_including_negative():
    assert neutralize(-1500) == -1500 and neutralize(0) == 0 and neutralize(3.5) == 3.5
    assert neutralize(None) == "" and neutralize(True) == "oui"


def test_render_uses_semicolon_bom_and_crlf():
    out = render(["mois", "montant_xof"], [("2026-01", 1200), ("=HYPERLINK(\"x\")", -5)])
    assert out.startswith(BOM)
    lines = out[len(BOM):].split("\r\n")
    assert lines[0] == "mois;montant_xof"
    assert lines[1] == "2026-01;1200"
    assert lines[2] == "\"'=HYPERLINK(\"\"x\"\")\";-5"


def test_csv_response_headers():
    r = csv_response("recettes mois/../x", ["a"], [(1,)])
    assert r.media_type == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"] == 'attachment; filename="recettes_mois_.._x.csv"'
    assert r.headers["cache-control"] == "no-store"
    assert r.body.startswith(BOM.encode())


def test_mask_is_recursive_in_dicts_and_lists():
    data = {"amount": 10, "password": "x", "nested": {"csrf_token": "t", "ok": 1,
            "items": [{"api_key": "k", "name": "n"}, {"Authorization": "Bearer z"}, "texte"]},
            "Secret": {"deep": "v"}, "hmac_signature": "s", "session_cookie": "c"}
    assert mask(data) == {"amount": 10, "password": MASK, "nested": {"csrf_token": MASK, "ok": 1,
                          "items": [{"api_key": MASK, "name": "n"}, {"Authorization": MASK}, "texte"]},
                          "Secret": MASK, "hmac_signature": MASK, "session_cookie": MASK}
    assert mask(None) is None and mask([1, 2]) == [1, 2]


def test_actor_normalization():
    admin = "00000000-0000-0000-0000-000000000001"
    assert actor_of("member.created", None, admin, "a@x.test") == ("a@x.test", "admin")
    # Une tentative de connexion n'est jamais attribuée au compte ciblé.
    assert actor_of("admin.login_failed", None, admin, "a@x.test") == (
        "Tentative de connexion sur le compte a@x.test (non authentifiée)", "attempt")
    assert actor_of("admin.login_failed", "anonymous", None, None)[1] == "attempt"
    assert actor_of("admin.login_blocked", None, admin, "a@x.test")[1] == "attempt"
    assert actor_of("quarantine.opened", "reconciler", None, None) == ("Réconciliateur (automatique)", "system")
    # CLI : actor_admin_id = compte concerné, jamais l'auteur.
    assert actor_of("admin.created", "cli", admin, "a@x.test") == ("Ligne de commande (serveur)", "cli")
    assert actor_of("x", None, None, None) == ("Inconnu", "unknown")
