import logging
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import get_settings
from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def liveness() -> dict[str, str]:
    """Le processus répond. Public, ne touche pas à la base (healthcheck Docker)."""
    return {"status": "ok"}


@router.get("/details", dependencies=[Depends(require_admin)])
def details(response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    db_ok = False
    server_version_num: int | None = None
    try:
        server_version_num = int(db.execute(text("SHOW server_version_num")).scalar_one())
        db_ok = True
    except Exception:
        logger.exception("health: échec de la vérification de la base")

    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if db_ok else "degraded",
        "version": settings.app_version,
        "database": {"ok": db_ok, "server_version_num": server_version_num},
        "mode": {
            "revocation_mode": settings.revocation_mode,
            "enforcer_enabled": settings.enforcer_enabled,
        },
    }
