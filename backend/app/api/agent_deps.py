"""Dépendance FastAPI : authentifie l'agent. Tout refus d'authentification renvoie le même 401."""
import logging
import re

from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_clock
from app.core.agent_protocol import H_AGENT, MAX_BODY_BYTES, NonceCache
from app.core.config import get_settings
from app.db.session import get_db
from app.domain.clock import Clock
from app.services.agent_auth import AgentAuthError, AgentContext, authenticate_agent, ip_allowed

logger = logging.getLogger("akwaba.agent")
REFUSED = "Authentification de l'agent refusée"
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_cache = NonceCache()      # un seul worker uvicorn : la mémoire du processus suffit


class BodyTooLarge(Exception):
    pass


def get_nonce_cache() -> NonceCache:
    return _cache


def get_peer_ip(request: Request):
    return request.client.host if request.client else None


def get_agent_networks() -> list:
    return get_settings().agent_allowed_ip_list


async def read_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge()
    size, chunks = 0, []
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


def _refuse(reason: str, ip, key_id: str) -> None:
    logger.warning("agent refusé reason=%s ip=%s key_id=%s", reason, ip, key_id)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, REFUSED)


async def require_agent(
    request: Request,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    cache: NonceCache = Depends(get_nonce_cache),
    peer_ip=Depends(get_peer_ip),
    networks=Depends(get_agent_networks),
) -> AgentContext:
    raw_key = request.headers.get(H_AGENT, "")
    key_id = raw_key if _SAFE_KEY.match(raw_key) else "-"    # jamais de texte arbitraire dans le journal
    if not ip_allowed(peer_ip, networks):
        _refuse("ip_not_allowed", peer_ip, key_id)
    try:
        body = await read_limited(request, MAX_BODY_BYTES)
    except BodyTooLarge:
        logger.warning("agent corps trop volumineux ip=%s", peer_ip)
        raise HTTPException(413, "Corps trop volumineux")
    try:
        return await run_in_threadpool(
            authenticate_agent, db, cache, clock, request.method, request.url.path,
            request.url.query, body, request.headers,
        )
    except AgentAuthError as exc:
        _refuse(exc.reason, peer_ip, key_id)
