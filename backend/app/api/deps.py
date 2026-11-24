import ipaddress

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import tokens_equal
from app.db.session import get_db
from app.domain.clock import Clock, SystemClock
from app.services.auth import AuthContext, load_session

COOKIE_NAME = "akwaba_session"


def get_clock() -> Clock:
    return SystemClock()


def client_ip(request: Request) -> str | None:
    """IP du client pour l'audit administrateur.

    X-Real-IP n'est lu que si la connexion TCP vient d'un proxy de confiance
    (TRUSTED_PROXY_IPS = l'IP fixe de Nginx). Nginx reconstruit toujours cet
    en-tête ; venant de n'importe qui d'autre, il est ignoré.

    Ne PAS utiliser pour l'agent : `agent_deps.get_peer_ip` lit l'IP TCP brute.
    """
    peer = request.client.host if request.client else None
    if peer is None:
        return None
    try:
        peer_n = str(ipaddress.ip_address(peer))
    except ValueError:
        return peer
    if peer_n not in get_settings().trusted_proxy_ip_set:
        return peer
    real = request.headers.get("x-real-ip", "").strip()
    try:
        return str(ipaddress.ip_address(real))
    except ValueError:
        return peer


def require_admin(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    token = request.cookies.get(COOKIE_NAME)
    ctx = load_session(db, get_settings(), token) if token else None
    if ctx is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non authentifié")
    return ctx


def require_csrf(request: Request, ctx: AuthContext = Depends(require_admin)) -> AuthContext:
    """À utiliser sur toute route qui modifie des données."""
    sent = request.headers.get("X-CSRF-Token", "")
    if not tokens_equal(sent, ctx.session.csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Jeton CSRF invalide")
    return ctx
