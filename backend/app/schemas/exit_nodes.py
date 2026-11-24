"""Lot 11d : diagnostic de l'agent / des Exit Nodes. Lecture seule.
Ne contient JAMAIS : secret HMAC, key_id, contenu des identifiants."""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class HeartbeatOut(BaseModel):
    received_at: datetime
    generated_at: Optional[datetime]
    agent_version: str
    tailscaled_version: Optional[str]
    conntrack_ok: bool
    status_ok: bool
    whois_ok: bool
    bytes_available: bool
    spool_depth: int
    spool_oldest_age_seconds: Optional[float]
    conntrack_count: Optional[int]
    conntrack_max: Optional[int]
    conntrack_saturation: Optional[float]
    conntrack_saturated: Optional[bool]
    clock_skew_seconds: Optional[int]          # mesuré par l'agent (réponse du backend)
    server_offset_seconds: Optional[float]     # réception − émission, mesuré par le serveur
    last_error: Optional[str]
    last_send_error: Optional[str]
    backend_reachable: Optional[bool]
    send_failures: Optional[int]
    rejected_batches: Optional[int]
    dropped_batches: Optional[int]
    loop_errors: Optional[int]


class CredentialSummary(BaseModel):
    active: int
    revoked: int
    last_used_at: Optional[datetime]


class LastError(BaseModel):
    at: datetime
    error: Optional[str]
    send_error: Optional[str]


class BatchOut(BaseModel):
    received_at: datetime
    window_start: datetime
    window_end: datetime
    sample_count: int
    observations: int


class ExitNodeOut(BaseModel):
    id: uuid.UUID
    name: str
    tailscale_ip: str
    is_active: bool
    healthy: bool                     # fonction du réconciliateur (quarantine._health)
    last_heartbeat: Optional[HeartbeatOut]
    last_error: Optional[LastError]
    credentials: CredentialSummary


class ExitNodePage(BaseModel):
    items: List[ExitNodeOut]
    evaluated_at: datetime
    health_max_age_seconds: int


class ExitNodeDetail(BaseModel):
    node: ExitNodeOut
    heartbeats: List[HeartbeatOut]    # dernières 24 h, plus récent d'abord
    batches: List[BatchOut]           # 20 derniers lots
    evaluated_at: datetime
    health_max_age_seconds: int
