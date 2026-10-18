"""Authentification d'un agent : en-têtes, fenêtre de temps, signature HMAC, révocation, anti-rejeu."""
import ipaddress
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Mapping, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.agent_protocol import (
    H_AGENT, H_NONCE, H_SIGNATURE, H_TIMESTAMP, NonceCache, check_timestamp, verify,
)
from app.core.crypto import CryptoError, decrypt_field
from app.domain.clock import Clock
from app.models.infra import AgentCredential, ExitNode

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TS_RE = re.compile(r"^[0-9]{1,12}$")
_NONCE_RE = re.compile(r"^[0-9a-f]{16,64}$")
_SIG_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DUMMY_SECRET = bytes(32)
_LAST_USED_REFRESH = timedelta(seconds=60)


class AgentAuthError(Exception):
    """Le motif sert au journal du serveur ; il n'est jamais renvoyé au client."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class AgentContext:
    exit_node_id: object
    exit_node_name: str
    key_id: str
    body: bytes        # corps déjà lu et signé : les routes l'utilisent, jamais request.body()
    now: float


def ip_allowed(ip: Optional[str], allowed: Sequence[str]) -> bool:
    if not ip or not allowed:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    for raw in allowed:
        try:
            if addr in ipaddress.ip_network(raw, strict=False):
                return True
        except ValueError:
            continue
    return False


def parse_headers(headers: Mapping[str, str]) -> Tuple[str, str, str, str]:
    key_id, ts = headers.get(H_AGENT), headers.get(H_TIMESTAMP)
    nonce, sig = headers.get(H_NONCE), headers.get(H_SIGNATURE)
    if not (key_id and ts and nonce and sig):
        raise AgentAuthError("missing_header")
    if not (_KEY_RE.match(key_id) and _TS_RE.match(ts) and _NONCE_RE.match(nonce) and _SIG_RE.match(sig)):
        raise AgentAuthError("malformed_header")
    return key_id, ts, nonce, sig


def authenticate_agent(
    db: Session, cache: NonceCache, clock: Clock, method: str, path: str, query: str,
    body: bytes, headers: Mapping[str, str],
) -> AgentContext:
    now_dt = clock.now()
    now = now_dt.timestamp()
    if query:
        raise AgentAuthError("unexpected_query")
    key_id, ts, nonce, sig = parse_headers(headers)
    state = check_timestamp(ts, now)
    if state != "ok":
        raise AgentAuthError(state)

    row = db.execute(
        select(AgentCredential, ExitNode)
        .join(ExitNode, ExitNode.id == AgentCredential.exit_node_id)
        .where(AgentCredential.key_id == key_id)
    ).first()
    if row is None:
        verify(_DUMMY_SECRET, method, path, body, ts, nonce, sig)   # même durée qu'un agent connu
        raise AgentAuthError("unknown_agent")
    cred, node = row
    try:
        secret = bytes.fromhex(decrypt_field(cred.secret_encrypted))
    except (CryptoError, ValueError):
        raise AgentAuthError("secret_unreadable")
    if not verify(secret, method, path, body, ts, nonce, sig):
        raise AgentAuthError("bad_signature")
    if cred.revoked_at is not None:
        raise AgentAuthError("revoked")
    if not node.is_active:
        raise AgentAuthError("node_inactive")
    if not cache.check_and_store(key_id, nonce, now):    # seulement après une signature valide
        raise AgentAuthError("replay")

    if cred.last_used_at is None or now_dt - cred.last_used_at > _LAST_USED_REFRESH:
        cred.last_used_at = now_dt
        db.commit()
    return AgentContext(exit_node_id=node.id, exit_node_name=node.name, key_id=key_id, body=body, now=now)
