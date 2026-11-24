"""Phase 12 — lot 12c : sécurité transversale (cahier des charges §7), de bout en bout.

Toutes les requêtes passent par la vraie chaîne TLS (Tailscale Serve simulé) → Nginx → backend → PostgreSQL.
L'inventaire des routes est lu dans l'application elle-même (schéma OpenAPI généré en mémoire) : une route
ajoutée plus tard est donc couverte automatiquement par les balayages ci-dessous.

    §7.1  aucune route sans session (sauf santé et connexion) ; déconnexion et expiration côté serveur ;
          verrouillage après échecs, réponses indiscernables ; cookie de session sécurisé
    §7.2  CSRF et Origin sur toutes les mutations ; champs inattendus rejetés ; erreurs cohérentes
    §7.3  pages réelles dans Chrome : CSP, Trusted Types, aucune requête externe, aucun gestionnaire inline,
          contenu utilisateur jamais interprété comme du HTML
    §7.4  en-têtes proxy falsifiés ignorés (X-Real-IP, X-Forwarded-For, Forwarded, Tailscale-User-*)
          ; l'API agent n'est jamais exposée par Nginx
    secrets  aucune réponse JSON ou CSV ne contient de secret (mots de passe, empreintes, jetons de session,
             secret HMAC, clés de chiffrement) ; key_id seulement là où c'est documenté
"""
import json
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from agent_harness import Admin, Client, Device, FakeExitNode, cli, create_agent, run_agent
from conftest import BACKEND, SKIP, TAILNET_CLIENT_IP, Backend, free_port, run_browser

pytestmark = SKIP

PUBLIC = {("GET", "/api/health"), ("POST", "/api/auth/login")}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
FAKE = "00000000-0000-4000-8000-000000000000"
XSS = '<img src=x onerror="window.__xss=1">'
MEMBER = Device("100.71.0.21", login="membre12c@example.com", user_id=2101, stable_id="nM21", hostname="tel-m", flows=4)
STRANGER = Device("100.71.0.22", login="inconnu12c@example.com", user_id=2102, stable_id="nS22", hostname="tel-s",
                  flows=4)
REFUSED_ORIGINS = [
    ("origine étrangère", {"Origin": "https://evil.example"}),
    ("origine null", {"Origin": "null"}),
    ("http au lieu de https", {"Origin": "http://localhost"}),
    ("sans Origin ni Referer", {}),
    ("Referer étranger", {"Referer": "https://evil.example/page"}),
    ("Sec-Fetch-Site cross-site", {"Sec-Fetch-Site": "cross-site", "Origin": None}),
]


def scalar(stack, sql, **params):
    with stack["engine"].connect() as c:
        return c.execute(text(sql), params).scalar()


def execute(stack, sql, **params):
    with stack["engine"].begin() as c:
        c.execute(text(sql), params)


@pytest.fixture(scope="module")
def OPS(stack):
    """(méthode, chemin, a un corps JSON) pour chaque opération de l'application, lues dans OpenAPI."""
    sys.path.insert(0, str(BACKEND))
    from app.main import create_app
    ops = []
    for path, item in create_app().openapi()["paths"].items():
        for method, op in item.items():
            ops.append((method.upper(), path, "requestBody" in op))
    return sorted(ops)


def concrete(path: str, ids: dict = None) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str((ids or {}).get(m.group(1)) or FAKE), path)


def admin_count(stack):
    return scalar(stack, "SELECT count(*) FROM audit_logs WHERE action NOT LIKE 'admin.login%'")


@pytest.fixture(scope="module")
def world(stack):
    """Données réelles pour que chaque page et chaque route de détail ait quelque chose à montrer."""
    node = FakeExitNode()
    node.configure([MEMBER, STRANGER])
    admin = Admin(stack)
    plan = next(p for p in admin.get("/api/plans") if not p["is_unlimited"] and p["duration_months"] == 1)
    method = admin.get("/api/payment-methods")[0]["id"]
    m = admin.post("/api/members", {"full_name": f"{XSS}Zed Injection", "email": "zed@example.com",
                                    "phone_e164": "+2250700000000", "notes": "<script>window.__xss=2</script>"})
    admin.post("/api/subscriptions", {"member_id": m["id"], "plan_id": plan["id"],
                                      "starts_on": (date.today() - timedelta(days=27)).isoformat(),
                                      "payment": {"payment_method_id": method,
                                                  "paid_at": datetime.now(timezone.utc).isoformat()}})
    admin.post(f"/api/members/{m['id']}/identities", {"login_name": MEMBER.login})
    cat = admin.get("/api/expense-categories")[0]["id"]
    admin.post("/api/expenses", {"category_id": cat, "amount": 15000, "expense_date": date.today().isoformat(),
                                 "description": f"{XSS}Serveur"})
    admin.post("/api/protected-identities", {"login_name": "ops12c@example.com", "reason": f"{XSS}motif"})
    agent = create_agent(stack, node, "exit-e2e-12c", "100.85.214.21")
    res = run_agent(node, agent, "--cycles", "3", "--interval", "1", "--window", "60", "--quiet",
                    "--spool", str(node.base / "spool.db"))
    assert res.returncode == 0, res.stdout + res.stderr
    assert cli(stack, "reconcile").returncode == 0
    record = scalar(stack, "SELECT r.id FROM quarantine_records r JOIN exit_node_identities i "
                           "ON i.id = r.exit_node_identity_id WHERE i.login_name = :l", l=STRANGER.login)
    admin.post(f"/api/quarantine/{record}/authorize", {"note": f"{XSS}invité"})
    admin.post("/api/reminders/generate")
    ids = {
        "member_id": m["id"],
        "sub_id": scalar(stack, "SELECT id FROM subscriptions LIMIT 1"),
        "payment_id": scalar(stack, "SELECT id FROM payments LIMIT 1"),
        "expense_id": scalar(stack, "SELECT id FROM expenses LIMIT 1"),
        "category_id": cat,
        "identity_id": scalar(stack, "SELECT id FROM exit_node_identities WHERE login_name = :l", l=MEMBER.login),
        "record_id": record,
        "exemption_id": scalar(stack, "SELECT id FROM identity_exemptions LIMIT 1"),
        "protected_id": scalar(stack, "SELECT id FROM protected_identities LIMIT 1"),
        "node_id": scalar(stack, "SELECT id FROM exit_nodes LIMIT 1"),
        "reminder_id": scalar(stack, "SELECT id FROM reminders LIMIT 1"),
    }
    yield {"admin": admin, "agent": agent, "ids": ids, "member": m}
    node.close()


# --------------------------------------------------------------------------- §7.1 autorisation

def test_route_inventory_is_complete(OPS):
    assert len(OPS) >= 60, OPS
    assert PUBLIC <= {(m, p) for m, p, _ in OPS}
    agent = [p for _, p, _ in OPS if p.startswith("/api/agent")]
    assert sorted(agent) == ["/api/agent/batches", "/api/agent/heartbeat", "/api/agent/ping"]


# --------------------------------------------------------------------------- §7.3 navigateur
# (en premier : une seule connexion avant elle, la limite de Nginx sur /api/auth/login est intacte)

def test_every_page_in_a_real_browser(stack, world):
    ids = world["ids"]
    run_browser(stack, "browser_12c.mjs", json.dumps({
        "member": str(world["member"]["id"]), "identity": str(ids["identity_id"]), "record": str(ids["record_id"])}))


def test_without_session_every_route_is_refused(stack, world, OPS):
    anon = Client(stack)
    bodies, seen = set(), 0
    for method, path, _ in OPS:
        if (method, path) in PUBLIC:
            continue
        url = concrete(path, world["ids"])
        status, headers, body = anon.request(method, url, {} if method in MUTATING else None,
                                             {"Origin": stack["base"]} if method in MUTATING else {})
        if path.startswith("/api/agent"):
            assert status == 404, (method, path, status)        # Nginx : jamais exposée sur 8443
            continue
        assert status == 401, (method, path, status, body)
        assert headers.get("Content-Type", "").startswith("application/json"), (path, headers)
        assert headers.get("X-Content-Type-Options") == "nosniff", path
        bodies.add(body)
        seen += 1
    assert seen >= 55
    assert bodies == {'{"detail":"Non authentifié"}'}, bodies
    assert anon.request("GET", "/api/health")[0] == 200
    assert anon.request("GET", "/api/health/details")[0] == 401


def test_logout_revokes_the_session_server_side(stack):
    c = Admin(stack)
    token = c.cookie("akwaba_session")
    status, headers, _ = c.request("POST", "/api/auth/logout", {}, {"Origin": stack["base"], "X-CSRF-Token": c.csrf})
    assert status == 204
    assert "Max-Age=0" in headers.get("Set-Cookie", "") or "expires=Thu, 01 Jan 1970" in headers.get("Set-Cookie", "")
    replay = Client(stack)                                  # cookie volé rejoué après la déconnexion
    status, _, _ = replay.request("GET", "/api/auth/me", headers={"Cookie": f"akwaba_session={token}"})
    assert status == 401
    assert scalar(stack, "SELECT revoked_at IS NOT NULL FROM admin_sessions WHERE token_hash = encode(sha256(:t), 'hex')",
                  t=token.encode()) is True


def _session_after(stack, sql):
    c = Admin(stack)
    assert c.request("GET", "/api/auth/me")[0] == 200
    execute(stack, f"UPDATE admin_sessions SET {sql} WHERE token_hash = encode(sha256(:t), 'hex')",
            t=c.cookie("akwaba_session").encode())
    return c.request("GET", "/api/auth/me")[0]


def test_idle_session_expires_server_side(stack):
    assert _session_after(stack, "last_seen_at = now() - interval '12 hours 1 minute'") == 401


def test_session_absolute_lifetime_is_enforced(stack):
    assert _session_after(stack, "expires_at = now() - interval '1 second'") == 401


def test_session_cookie_attributes(stack):
    c = Client(stack)
    status, headers, _ = c.request("POST", "/api/auth/login", {"email": stack["email"], "password": stack["password"]},
                                   {"Origin": stack["base"]})
    assert status == 200
    cookie = headers.get("Set-Cookie", "")
    for attr in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/api"):
        assert attr.lower() in cookie.lower(), (attr, cookie)
    assert "domain=" not in cookie.lower()


def test_brute_force_lockout_is_indistinguishable(stack):
    sys.path.insert(0, str(BACKEND))
    from app.core.security import hash_password
    email, password = f"lock-{uuid.uuid4().hex[:6]}@example.test", "mot-de-passe-solide-12c"
    execute(stack, "INSERT INTO admin_users (email, password_hash, is_active, display_name) "
                   "VALUES (:e, :h, true, 'Cible')", e=email, h=hash_password(password))
    c = Client(stack)
    origin = {"Origin": stack["base"]}
    answers = [c.request("POST", "/api/auth/login", {"email": email, "password": "faux-mot-de-passe-1234"}, origin)
               for _ in range(5)]
    locked = c.request("POST", "/api/auth/login", {"email": email, "password": password}, origin)
    unknown = c.request("POST", "/api/auth/login", {"email": "personne@example.test", "password": password}, origin)
    assert {(s, b) for s, _, b in answers + [locked, unknown]} == {(401, '{"detail":"Identifiants invalides"}')}
    assert "Set-Cookie" not in locked[1]
    uid = scalar(stack, "SELECT id FROM admin_users WHERE email = :e", e=email)
    assert scalar(stack, "SELECT count(*) FROM audit_logs WHERE action = 'admin.login_failed' AND actor_admin_id = :u",
                  u=uid) == 5
    assert scalar(stack, "SELECT count(*) FROM audit_logs WHERE action = 'admin.login_blocked' AND actor_admin_id = :u",
                  u=uid) == 1
    assert scalar(stack, "SELECT locked_until > now() FROM admin_users WHERE id = :u", u=uid)


# --------------------------------------------------------------------------- §7.2 CSRF, Origin, validation

def _direct(url, payload, headers):
    """Requête HTTP directe sur le backend (sans Nginx), sans proxy système."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, method="POST", data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_every_mutation_requires_csrf_and_a_same_origin(stack, world, OPS):
    admin = world["admin"]
    before = admin_count(stack)
    checked = 0
    for method, path, _ in OPS:
        if method not in MUTATING or path.startswith("/api/agent") or (method, path) in PUBLIC:
            continue
        url = concrete(path)
        same = {"Origin": stack["base"]}
        for label, headers in [("sans jeton CSRF", same), ("jeton CSRF faux", {**same, "X-CSRF-Token": "x" * 43})]:
            status, _, body = admin.request(method, url, {}, headers)
            assert (status, body) == (403, '{"detail":"Jeton CSRF invalide"}'), (method, path, label, status, body)
        for label, extra in REFUSED_ORIGINS:
            headers = {"X-CSRF-Token": admin.csrf, **{k: v for k, v in extra.items() if v is not None}}
            if label == "Sec-Fetch-Site cross-site":
                headers["Origin"] = stack["base"]
            status, _, body = admin.request(method, url, {}, headers)
            assert (status, json.loads(body)) == (403, {"detail": "Origine de la requête refusée"}), (method, path, label, status)
        checked += 1
    assert checked >= 24
    # La connexion elle-même vérifie l'origine (middleware du backend ; appel direct pour ne pas consommer
    # la limite de débit de Nginx sur /api/auth/login, qui n'intervient pas dans ce contrôle).
    direct = f"http://127.0.0.1:{stack['backend_port']}"
    for label, extra in REFUSED_ORIGINS:
        headers = {"Content-Type": "application/json", **{k: v for k, v in extra.items() if v is not None}}
        if label == "Sec-Fetch-Site cross-site":
            headers["Origin"] = stack["base"]
        status, body = _direct(direct + "/api/auth/login", {"email": stack["email"], "password": stack["password"]},
                               headers)
        assert (status, json.loads(body)) == (403, {"detail": "Origine de la requête refusée"}), (label, status, body)
    assert admin_count(stack) == before, "aucune mutation refusée ne laisse de trace métier"


def test_unexpected_fields_are_rejected_everywhere(stack, world, OPS):
    admin = world["admin"]
    before = admin_count(stack)
    checked = []
    for method, path, has_body in OPS:
        if not has_body or path.startswith("/api/agent") or (method, path) in PUBLIC:
            continue
        status, _, body = admin.request(method, concrete(path, world["ids"]), {"champ_inattendu": 1},
                                        {"Origin": stack["base"], "X-CSRF-Token": admin.csrf})
        assert status == 422, (method, path, status, body)
        assert "champ_inattendu" in body and "Traceback" not in body
        checked.append(path)
    assert len(checked) >= 15, checked
    assert admin_count(stack) == before


# --------------------------------------------------------------------------- §7.4 en-têtes proxy

SPOOF = {"X-Real-IP": "203.0.113.66", "X-Forwarded-For": "203.0.113.67", "Forwarded": "for=203.0.113.68",
         "Tailscale-User-Login": "pirate@example.com", "Tailscale-User-Name": "Pirate"}


def last_login_ip(stack):
    return scalar(stack, "SELECT ip_address::text FROM audit_logs WHERE action = 'admin.login_success' "
                         "ORDER BY occurred_at DESC, id DESC LIMIT 1")


def test_forged_proxy_headers_are_ignored(stack):
    c = Client(stack)
    status, _, _ = c.request("POST", "/api/auth/login", {"email": stack["email"], "password": stack["password"]},
                             {"Origin": stack["base"], **SPOOF})
    assert status == 200
    # Nginx ne fait confiance qu'au saut Tailscale Serve et garde la dernière adresse de la chaîne : celle
    # que Serve atteste. Les valeurs fournies par le client ne sont jamais retenues.
    assert last_login_ip(stack).split("/")[0] == TAILNET_CLIENT_IP
    assert "203.0.113" not in scalar(stack, "SELECT string_agg(coalesce(ip_address::text, ''), ',') FROM audit_logs")


def _external_ip():
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.254", 9))            # aucun paquet envoyé : choisit l'interface sortante
        ip = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if ip.startswith("127.") else ip


def _login_direct_with_spoof(stack, host, connect_to):
    port = free_port()
    other = Backend({**stack["backend"].env, "TRUSTED_PROXY_IPS": "10.250.11.10"}, port,
                    stack["work"] / "backend-proxy.log", host=host).start()
    try:
        status, body = _direct(f"http://{connect_to}:{port}/api/auth/login",
                               {"email": stack["email"], "password": stack["password"]},
                               {"Content-Type": "application/json", "Origin": stack["base"], **SPOOF})
        assert status == 200, body
    finally:
        other.stop()
    return last_login_ip(stack).split("/")[0]


def test_x_real_ip_from_an_untrusted_peer_is_ignored(stack):
    """Comme en production : un client atteint le backend depuis une IP qui n'est ni Nginx ni le loopback
    du conteneur. X-Real-IP, X-Forwarded-For et Forwarded sont ignorés ; l'audit garde l'IP TCP."""
    ip = _external_ip()
    if ip is None:
        pytest.skip("aucune adresse IPv4 hors loopback sur cette machine")
    assert _login_direct_with_spoof(stack, "0.0.0.0", ip) == ip


def test_uvicorn_ignores_forwarded_headers_even_from_loopback(stack):
    """Issue #8, corrigée en Phase 13a : le conteneur lance uvicorn avec --no-proxy-headers (le harnais
    reprend les options du Dockerfile). Même depuis le loopback, X-Forwarded-For ne réécrit plus l'IP client ;
    seule l'application décide (TRUSTED_PROXY_IPS + X-Real-IP reconstruit par Nginx)."""
    assert _login_direct_with_spoof(stack, "127.0.0.1", "127.0.0.1") == "127.0.0.1"


def test_agent_ip_allowlist_cannot_be_bypassed_with_forwarded_headers(stack, world):
    """Issue #8 : la liste d'IP de l'agent lit l'adresse TCP ; un X-Forwarded-For portant une IP autorisée,
    envoyé depuis une adresse non autorisée (ici le loopback), reste refusé."""
    from akwaba_agent.protocol import build_headers
    from akwaba_agent.sender.client import HttpTransport
    port = free_port()
    other = Backend({**stack["backend"].env, "AGENT_ALLOWED_IPS": "100.64.0.0/10"}, port,
                    stack["work"] / "backend-agent-xff.log").start()
    try:
        agent = world["agent"]
        headers = build_headers(agent["key_id"], bytes.fromhex(agent["secret"]), "POST", "/api/agent/ping", b"{}")
        headers.update({"X-Forwarded-For": "100.64.0.9", "X-Real-IP": "100.64.0.9", "Forwarded": "for=100.64.0.9"})
        reply = HttpTransport(f"http://127.0.0.1:{port}", 10).post("/api/agent/ping", b"{}", headers)
    finally:
        other.stop()
    assert reply.status == 401
    log = (stack["work"] / "backend-agent-xff.log").read_text()
    assert "reason=ip_not_allowed ip=127.0.0.1" in log, log[-500:]


# --------------------------------------------------------------------------- secrets

def test_no_secret_in_any_json_or_csv_response(stack, world, OPS):
    admin = world["admin"]
    env = stack["backend"].env
    with stack["engine"].connect() as c:
        hashes = c.execute(text("SELECT password_hash FROM admin_users")).scalars().all()
        token_hashes = c.execute(text("SELECT token_hash FROM admin_sessions")).scalars().all()
        encrypted = c.execute(text("SELECT secret_encrypted FROM agent_credentials")).scalars().all()
        key_ids = c.execute(text("SELECT key_id FROM agent_credentials")).scalars().all()
    forbidden = {
        "mot de passe administrateur": [stack["password"]],
        "empreinte de mot de passe": hashes,
        "jeton de session": [admin.cookie("akwaba_session")],
        "empreinte de jeton de session": token_hashes,
        "secret HMAC de l'agent": [world["agent"]["secret"]],
        "secret HMAC chiffré": encrypted,
        "SECRET_KEY": [env["SECRET_KEY"]],
        "FIELD_ENCRYPTION_KEY": [env["FIELD_ENCRYPTION_KEY"]],
    }
    forbidden_keys = {"password", "password_hash", "token", "token_hash", "secret", "secret_encrypted", "session_token"}

    def walk(node, where, found):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.lower() in forbidden_keys:
                    found.append(f"{where}: clé {k}")
                walk(v, where, found)
        elif isinstance(node, list):
            for v in node:
                walk(v, where, found)

    crawled, problems = [], []
    for method, path, _ in OPS:
        if method != "GET":
            continue
        urls = [concrete(path, world["ids"])]
        if path in ("/api/expenses", "/api/reports/revenue", "/api/reports/revenue-by-plan", "/api/reports/expenses",
                    "/api/reports/profit", "/api/reports/operations"):
            urls.append(urls[0] + "?format=csv")
        for url in urls:
            status, headers, body = admin.request("GET", url)
            assert status == 200, (url, status, body)
            crawled.append(url)
            for label, values in forbidden.items():
                if any(v and v in body for v in values):
                    problems.append(f"{url}: {label}")
            if admin.csrf in body and path != "/api/auth/me":
                problems.append(f"{url}: jeton CSRF")
            if "json" in headers.get("Content-Type", ""):
                data = json.loads(body)
                walk(data, url, problems)
                if any(k in body for k in key_ids):
                    # Seule exception documentée (lot 11d) : l'identifiant d'entité des événements d'audit
                    # agent.credential_* (événements append-only, jamais réécrits).
                    items = data.get("items", []) if isinstance(data, dict) else []
                    allowed = {e["entity_id"] for e in items if e.get("action", "").startswith("agent.credential_")}
                    leftover = body
                    for k in allowed:
                        leftover = leftover.replace(f'"entity_id":"{k}"', "")
                    if any(k in leftover for k in key_ids):
                        problems.append(f"{url}: key_id hors des événements d'audit agent.credential_*")
            else:
                assert headers.get("Content-Type", "").startswith("text/csv"), url
    assert len(crawled) >= 35, crawled
    assert problems == [], problems


def test_nginx_rate_limits_the_login_endpoint(stack):
    """Limite de débit de Nginx sur /api/auth/login (10/min, rafale 5) : les requêtes en excès reçoivent
    429 de Nginx et n'atteignent jamais le backend (aucun événement d'audit supplémentaire)."""
    c = Client(stack)
    before = scalar(stack, "SELECT count(*) FROM audit_logs WHERE action LIKE 'admin.login%'")
    statuses = [c._request_once("POST", "/api/auth/login", {"email": "flood@example.test", "password": "x" * 12},
                                {"Origin": stack["base"]})[0] for _ in range(12)]
    assert 429 in statuses and set(statuses) <= {401, 429}, statuses
    reached = statuses.count(401)
    assert reached <= 6, statuses
    after = scalar(stack, "SELECT count(*) FROM audit_logs WHERE action LIKE 'admin.login%'")
    assert after - before == reached, "seules les requêtes non limitées atteignent le backend"


def test_dry_run_is_untouched(stack):
    assert scalar(stack, "SELECT count(*) FROM enforcement_decisions WHERE mode <> 'dry_run' OR enforced") == 0
