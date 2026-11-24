"""Lot 11a : vérification stricte de l'origine (complément du jeton CSRF)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.origin import OriginCheckMiddleware, check_origin, normalize_origin
from app.main import create_app

DASH = "https://ubuntu.tail14614.ts.net:8443"
ALLOWED = frozenset({DASH})


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OriginCheckMiddleware, allowed_origins=[DASH])

    @app.post("/api/members")
    def mutate() -> dict:
        return {"ok": True}

    @app.put("/api/members/1")
    def put() -> dict:
        return {"ok": True}

    @app.patch("/api/members/1")
    def patch() -> dict:
        return {"ok": True}

    @app.delete("/api/members/1")
    def delete() -> dict:
        return {"ok": True}

    @app.get("/api/members")
    def read() -> dict:
        return {"ok": True}

    @app.post("/api/agent/ping")
    def agent() -> dict:
        return {"agent": True}

    @app.post("/api/agentx")
    def not_agent() -> dict:
        return {"ok": True}

    return app


@pytest.fixture
def client():
    with TestClient(_app(), base_url="https://testserver") as c:
        yield c


# --- normalisation ------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("https://Ubuntu.Tail14614.ts.net:8443", DASH),
    ("https://ubuntu.tail14614.ts.net:8443/membres?x=1", DASH),
    ("https://ubuntu.tail14614.ts.net:443", "https://ubuntu.tail14614.ts.net"),
    ("https://ubuntu.tail14614.ts.net", "https://ubuntu.tail14614.ts.net"),
    ("http://ubuntu.tail14614.ts.net:8443", None),
    ("https://user:pw@ubuntu.tail14614.ts.net:8443", None),
    ("https://ubuntu.tail14614.ts.net:99999", None),
    ("null", None),
    ("", None),
])
def test_normalize_origin(raw, expected):
    assert normalize_origin(raw) == expected


# --- règles -------------------------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutation_with_dashboard_origin_is_accepted(client, method):
    r = client.request(method, "/api/members" if method == "POST" else "/api/members/1", headers={"Origin": DASH})
    assert r.status_code == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutation_without_origin_nor_referer_is_refused(client, method):
    r = client.request(method, "/api/members" if method == "POST" else "/api/members/1")
    assert r.status_code == 403
    assert r.json() == {"detail": "Origine de la requête refusée"}
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("origin", [
    "https://ubuntu.tail14614.ts.net",          # Funnel public (port 443) : même site, autre origine
    "http://ubuntu.tail14614.ts.net:8443",      # pas HTTPS
    "https://evil.example",
    "https://ubuntu.tail14614.ts.net:8443.evil.example",
    "null",
])
def test_foreign_origin_is_refused(client, origin):
    assert client.post("/api/members", headers={"Origin": origin}).status_code == 403


def test_referer_is_used_when_origin_is_absent(client):
    assert client.post("/api/members", headers={"Referer": DASH + "/#/membres"}).status_code == 200
    assert client.post("/api/members", headers={"Referer": "https://ubuntu.tail14614.ts.net/x"}).status_code == 403


def test_origin_takes_precedence_over_referer(client):
    r = client.post("/api/members", headers={"Origin": "https://evil.example", "Referer": DASH + "/"})
    assert r.status_code == 403


@pytest.mark.parametrize("site, expected", [
    ("same-origin", 200), ("same-site", 403), ("cross-site", 403), ("none", 403),
])
def test_sec_fetch_site(client, site, expected):
    r = client.post("/api/members", headers={"Origin": DASH, "Sec-Fetch-Site": site})
    assert r.status_code == expected


def test_safe_methods_are_not_checked(client):
    assert client.get("/api/members").status_code == 200
    assert client.get("/api/members", headers={"Origin": "https://evil.example"}).status_code == 200


def test_agent_routes_are_exempt(client):
    # L'agent n'envoie ni Origin ni Referer : son authentification HMAC reste seule juge.
    assert client.post("/api/agent/ping").status_code == 200
    assert client.post("/api/agent/ping", headers={"Origin": "https://evil.example"}).status_code == 200


def test_agent_prefix_is_exact(client):
    assert client.post("/api/agentx").status_code == 403


def test_check_origin_unknown_unsafe_method_is_checked():
    assert check_origin("PROPFIND", "/api/x", {}, ALLOWED) == "origin_missing"


def test_middleware_requires_at_least_one_origin():
    with pytest.raises(ValueError):
        OriginCheckMiddleware(lambda *a: None, allowed_origins=[])


# --- application réelle -------------------------------------------------------

def test_real_app_refuses_login_without_origin():
    """Login CSRF : la connexion est elle aussi une requête mutative contrôlée."""
    with TestClient(create_app(), base_url="https://testserver") as c:
        r = c.post("/api/auth/login", json={"email": "a@b.c", "password": "x"})
    assert r.status_code == 403


def test_real_app_refuses_login_from_funnel_origin():
    with TestClient(create_app(), base_url="https://testserver") as c:
        r = c.post("/api/auth/login", json={"email": "a@b.c", "password": "x"},
                   headers={"Origin": "https://ubuntu.tail14614.ts.net"})
    assert r.status_code == 403


def test_real_app_agent_route_reaches_hmac_check_without_origin():
    """Sans Origin, /api/agent/ping doit répondre 401 (HMAC) et non 403 (origine)."""
    app = create_app()
    from app.api import agent_deps
    from app.db.session import get_db
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[agent_deps.get_agent_networks] = lambda: []
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/api/agent/ping")
    assert r.status_code == 401
