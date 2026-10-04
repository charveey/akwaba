from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import tokens_equal
from app.db.session import get_db
from app.services.auth import AuthContext, load_session

COOKIE_NAME = "akwaba_session"


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
