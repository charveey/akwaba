"""Lot 11c — garantie statique : le backend n'exécute aucune action réseau réelle.

Aucun sous-processus, aucune commande Tailscale / nftables / iptables, aucun socket brut,
et la configuration interdit tout autre mode que dry_run.
"""
import pathlib
import re

import pytest

from app.core.config import Settings

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
SOURCES = sorted(APP.rglob("*.py"))

FORBIDDEN = {
    "sous-processus": re.compile(r"\bimport\s+subprocess\b|\bfrom\s+subprocess\b|\bsubprocess\."),
    "os.system / exec": re.compile(r"\bos\.(system|popen|exec\w*|spawn\w*)\b"),
    "socket brut": re.compile(r"\bimport\s+socket\b|\bfrom\s+socket\b"),
    "commande nftables / iptables": re.compile(r"\b(nft|iptables|ip6tables)\b"),
    "commande tailscale": re.compile(r"[\"']tailscale\s+(up|down|set|lock|logout|serve|funnel|file)\b"),
    "écriture API Tailscale": re.compile(r"\.(post|put|patch|delete)\(\s*[\"']https://api\.tailscale\.com"),
}


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
def test_backend_contains_no_network_action(name):
    pattern = FORBIDDEN[name]
    hits = []
    for path in SOURCES:
        for no, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if pattern.search(code):
                hits.append(f"{path.relative_to(APP.parent)}:{no}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_sources_were_scanned():
    assert len(SOURCES) > 40


BASE = {"database_url": "postgresql+psycopg://u:p@db:5432/x", "secret_key": "s" * 48,
        "field_encryption_key": "A" * 43 + "=", "allowed_origins": "https://a.ts.net:8443"}


def test_only_dry_run_is_accepted():
    s = Settings(**BASE)
    assert (s.revocation_mode, s.enforcer_enabled) == ("dry_run", False)
    with pytest.raises(ValueError):
        Settings(**BASE, revocation_mode="enforce")
    with pytest.raises(ValueError):
        Settings(**BASE, enforcer_enabled=True)
