"""Lot 11a : IP client derrière Nginx. Seule l'IP fixe de Nginx est un proxy de confiance.

L'agent n'est pas concerné : son filtrage IP lit toujours l'adresse TCP brute.
"""
import pytest
from starlette.requests import Request

from app.api import deps
from app.api.agent_deps import get_peer_ip
from app.core.config import Settings, get_settings

NGINX = "10.250.11.10"


def _request(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http", "method": "GET", "path": "/", "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 12345) if peer else None,
    }
    return Request(scope)


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", NGINX)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def untrusted(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_nginx_real_ip_is_used(trusted):
    assert deps.client_ip(_request(NGINX, {"X-Real-IP": "100.101.102.103"})) == "100.101.102.103"


def test_x_forwarded_for_is_never_read(trusted):
    r = _request(NGINX, {"X-Forwarded-For": "1.2.3.4"})
    assert deps.client_ip(r) == NGINX


def test_header_from_untrusted_peer_is_ignored(trusted):
    # Ex. client du tailnet qui appelle directement 100.126.200.88:8000 (via la passerelle Docker).
    r = _request("10.250.11.1", {"X-Real-IP": "1.2.3.4"})
    assert deps.client_ip(r) == "10.250.11.1"


def test_invalid_real_ip_falls_back_to_peer(trusted):
    assert deps.client_ip(_request(NGINX, {"X-Real-IP": "pas-une-ip"})) == NGINX
    assert deps.client_ip(_request(NGINX, {"X-Real-IP": "1.2.3.4, 5.6.7.8"})) == NGINX


def test_no_trusted_proxy_by_default(untrusted):
    assert deps.client_ip(_request(NGINX, {"X-Real-IP": "1.2.3.4"})) == NGINX


def test_no_client(trusted):
    assert deps.client_ip(_request(None)) is None


def test_agent_peer_ip_ignores_proxy_headers(trusted):
    """Le filtrage IP de l'agent (phase 7/8) reste sur l'adresse TCP, même depuis Nginx."""
    r = _request(NGINX, {"X-Real-IP": "100.85.214.5", "X-Forwarded-For": "100.85.214.5"})
    assert get_peer_ip(r) == NGINX


# --- configuration -------------------------------------------------------------

BASE = {
    "database_url": "postgresql+psycopg://u:p@db:5432/x",
    "secret_key": "s" * 48,
    "field_encryption_key": "A" * 43 + "=",
    "allowed_origins": "https://ubuntu.tail14614.ts.net:8443",
}


def test_trusted_proxy_must_be_single_ip():
    with pytest.raises(ValueError):
        Settings(**{**BASE, "trusted_proxy_ips": "10.250.11.0/24"})
    s = Settings(**{**BASE, "trusted_proxy_ips": " 10.250.11.10 "})
    assert s.trusted_proxy_ip_set == frozenset({"10.250.11.10"})


@pytest.mark.parametrize("value", [
    "", " , ", "http://ubuntu.tail14614.ts.net:8443", "https://ubuntu.tail14614.ts.net:8443/",
    "https://ubuntu.tail14614.ts.net:8443/api", "*",
])
def test_allowed_origins_rejects_invalid(value):
    with pytest.raises(ValueError):
        Settings(**{**BASE, "allowed_origins": value})


def test_allowed_origins_list():
    s = Settings(**{**BASE, "allowed_origins": "https://a.ts.net:8443, https://b.ts.net"})
    assert s.allowed_origin_list == ["https://a.ts.net:8443", "https://b.ts.net"]


def test_allowed_origins_is_required(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    with pytest.raises(ValueError):
        Settings(**{k: v for k, v in BASE.items() if k != "allowed_origins"})
