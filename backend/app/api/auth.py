from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_NAME, require_admin, require_csrf
from app.core.config import get_settings
from app.db.session import get_db
from app.services.auth import AuthContext, authenticate, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class AdminInfo(BaseModel):
    email: str
    display_name: str | None
    csrf_token: str


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login", response_model=AdminInfo)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> AdminInfo:
    settings = get_settings()
    result = authenticate(
        db, settings, body.email, body.password, _client_ip(request), request.headers.get("user-agent"),
    )
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
    response.set_cookie(
        COOKIE_NAME, result.raw_token,
        max_age=settings.session_absolute_days * 86400,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/api",
    )
    response.headers["Cache-Control"] = "no-store"
    return AdminInfo(
        email=result.admin.email, display_name=result.admin.display_name, csrf_token=result.session.csrf_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, ctx: AuthContext = Depends(require_csrf), db: Session = Depends(get_db),
) -> None:
    revoke_session(db, ctx, _client_ip(request))
    settings = get_settings()
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, secure=settings.cookie_secure, samesite="lax")


@router.get("/me", response_model=AdminInfo)
def me(response: Response, ctx: AuthContext = Depends(require_admin)) -> AdminInfo:
    response.headers["Cache-Control"] = "no-store"
    return AdminInfo(email=ctx.admin.email, display_name=ctx.admin.display_name, csrf_token=ctx.session.csrf_token)
