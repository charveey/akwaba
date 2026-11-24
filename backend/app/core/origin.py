"""Vérification stricte de l'origine des requêtes mutatives (défense CSRF en profondeur).

Complète le jeton CSRF de session (`require_csrf`), sans le remplacer.

Règles, pour POST / PUT / PATCH / DELETE (et toute méthode non sûre) :

* `/api/agent` et `/api/agent/*` sont exclus : l'agent n'est pas un navigateur,
  il s'authentifie par HMAC + liste d'IP (inchangé).
* `Sec-Fetch-Site`, s'il est présent, doit valoir `same-origin`.
* `Origin`, s'il est présent, doit appartenir à ALLOWED_ORIGINS (`null` est refusé).
* Sans `Origin`, l'origine est déduite de `Referer`, qui doit appartenir à ALLOWED_ORIGINS.
* Sans `Origin` ni `Referer` : refus (403). Les navigateurs actuels envoient
  toujours `Origin` sur une requête mutative ; seuls des clients non navigateurs
  peuvent l'omettre, et ils doivent alors l'ajouter explicitement.

Middleware ASGI pur : il ne lit jamais le corps de la requête (la vérification
HMAC de l'agent reçoit le flux intact).
"""
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

logger = logging.getLogger("akwaba.security")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
REFUSED = "Origine de la requête refusée"

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


def normalize_origin(value: str) -> str | None:
    """Ramène une origine (ou une URL) à `https://hôte[:port]` en minuscules ; None si invalide."""
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        return None
    host = host.lower()
    if port is None or port == 443:
        return f"https://{host}"
    return f"https://{host}:{port}"


def normalize_origins(values: Iterable[str]) -> frozenset[str]:
    out = set()
    for v in values:
        n = normalize_origin(v)
        if n is None:
            raise ValueError(f"origine invalide : {v!r}")
        out.add(n)
    return frozenset(out)


def is_agent_path(path: str) -> bool:
    return path == "/api/agent" or path.startswith("/api/agent/")


def check_origin(method: str, path: str, headers: dict[str, str], allowed: frozenset[str]) -> str | None:
    """Renvoie None si la requête est acceptée, sinon le motif du refus (pour le journal)."""
    if method.upper() in SAFE_METHODS or is_agent_path(path):
        return None
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site.strip().lower() != "same-origin":
        return "sec_fetch_site"
    origin = headers.get("origin")
    if origin is not None:
        if origin.strip().lower() == "null":
            return "origin_null"
        return None if normalize_origin(origin) in allowed else "origin_mismatch"
    referer = headers.get("referer")
    if referer is not None:
        return None if normalize_origin(referer) in allowed else "referer_mismatch"
    return "origin_missing"


class OriginCheckMiddleware:
    def __init__(self, app: Callable, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed = normalize_origins(allowed_origins)
        if not self.allowed:
            raise ValueError("aucune origine autorisée")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = check_origin(scope["method"], scope["path"], headers, self.allowed)
        if reason is None:
            await self.app(scope, receive, send)
            return
        # Jamais la valeur reçue dans le journal (texte arbitraire) : seulement le motif.
        logger.warning("requête refusée reason=%s method=%s path=%r", reason, scope["method"][:16], scope["path"][:200])
        body = json.dumps({"detail": REFUSED}, ensure_ascii=False).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
