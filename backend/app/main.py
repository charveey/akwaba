import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, billing, finance, health, members
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.db.session import dispose_engine

logger = logging.getLogger("akwaba")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    logger.info(
        "démarrage version=%s revocation_mode=%s enforcer_enabled=%s",
        s.app_version, s.revocation_mode, s.enforcer_enabled,
    )
    yield
    dispose_engine()
    logger.info("arrêt")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/api/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
        lifespan=lifespan,
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(members.router, prefix="/api")
    app.include_router(billing.reference_router, prefix="/api")
    app.include_router(billing.subscriptions_router, prefix="/api")
    app.include_router(billing.payments_router, prefix="/api")
    app.include_router(finance.categories_router, prefix="/api")
    app.include_router(finance.expenses_router, prefix="/api")
    return app


app = create_app()
