"""Lot 11d : diagnostic des Exit Nodes. Lecture seule ; aucune commande, aucune action réseau.
La santé est celle du réconciliateur (quarantine._health, seuil HEALTH_MAX_AGE), jamais recalculée ici.
Les champs exposés sont une LISTE BLANCHE : aucun secret, aucun key_id, aucun contenu d'identifiant."""
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.clock import Clock
from app.models.infra import AgentBatch, AgentCredential, AgentHeartbeat, ExitNode, ExitNodeObservation
from app.schemas.exit_nodes import (
    BatchOut, CredentialSummary, ExitNodeDetail, ExitNodeOut, ExitNodePage, HeartbeatOut, LastError,
)
from app.services import quarantine

_DETAIL_KEYS = ("spool_oldest_age_seconds", "conntrack_saturation", "conntrack_saturated", "clock_skew_seconds",
                "last_send_error", "backend_reachable", "send_failures", "rejected_batches", "dropped_batches",
                "loop_errors")


def _parse(ts) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if isinstance(ts, str) else None
    except ValueError:
        return None


def _hb_out(hb: AgentHeartbeat) -> HeartbeatOut:
    d = hb.details if isinstance(hb.details, dict) else {}
    generated = _parse(d.get("generated_at"))
    return HeartbeatOut(
        received_at=hb.received_at, generated_at=generated, agent_version=hb.agent_version,
        tailscaled_version=hb.tailscaled_version, conntrack_ok=hb.conntrack_ok, status_ok=hb.status_ok,
        whois_ok=hb.whois_ok, bytes_available=hb.bytes_available, spool_depth=hb.spool_depth,
        conntrack_count=hb.conntrack_count, conntrack_max=hb.conntrack_max, last_error=hb.last_error,
        server_offset_seconds=(hb.received_at - generated).total_seconds() if generated else None,
        **{k: d.get(k) for k in _DETAIL_KEYS},
    )


def _node_out(db: Session, node: ExitNode, health: dict) -> ExitNodeOut:
    last = db.execute(select(AgentHeartbeat).where(AgentHeartbeat.exit_node_id == node.id)
                      .order_by(AgentHeartbeat.received_at.desc(), AgentHeartbeat.id.desc()).limit(1)).scalar_one_or_none()
    err = db.execute(select(AgentHeartbeat).where(
        AgentHeartbeat.exit_node_id == node.id,
        or_(AgentHeartbeat.last_error.is_not(None), AgentHeartbeat.details["last_send_error"].astext.is_not(None)))
        .order_by(AgentHeartbeat.received_at.desc(), AgentHeartbeat.id.desc()).limit(1)).scalar_one_or_none()
    active, revoked, used = db.execute(select(
        func.count().filter(AgentCredential.revoked_at.is_(None)),
        func.count().filter(AgentCredential.revoked_at.is_not(None)),
        func.max(AgentCredential.last_used_at)).where(AgentCredential.exit_node_id == node.id)).one()
    return ExitNodeOut(
        id=node.id, name=node.name, tailscale_ip=node.tailscale_ip, is_active=node.is_active,
        healthy=bool(health.get(node.id, False)),
        last_heartbeat=_hb_out(last) if last else None,
        last_error=LastError(at=err.received_at, error=err.last_error,
                             send_error=(err.details or {}).get("last_send_error")) if err else None,
        credentials=CredentialSummary(active=int(active), revoked=int(revoked), last_used_at=used),
    )


def list_nodes(db: Session, clock: Clock) -> ExitNodePage:
    now = clock.now()
    health = quarantine._health(db, now, quarantine.HEALTH_MAX_AGE)
    nodes = db.execute(select(ExitNode).order_by(ExitNode.name)).scalars().all()
    return ExitNodePage(items=[_node_out(db, n, health) for n in nodes], evaluated_at=now,
                        health_max_age_seconds=int(quarantine.HEALTH_MAX_AGE.total_seconds()))


def node_detail(db: Session, clock: Clock, node_id: uuid.UUID) -> ExitNodeDetail:
    node = db.get(ExitNode, node_id)
    if node is None:
        raise HTTPException(404, "Exit Node introuvable")
    now = clock.now()
    health = quarantine._health(db, now, quarantine.HEALTH_MAX_AGE)
    hbs = db.execute(select(AgentHeartbeat).where(
        AgentHeartbeat.exit_node_id == node.id, AgentHeartbeat.received_at >= now - timedelta(hours=24))
        .order_by(AgentHeartbeat.received_at.desc(), AgentHeartbeat.id.desc())).scalars().all()
    obs = (select(func.count()).where(ExitNodeObservation.batch_id == AgentBatch.batch_id)
           .correlate(AgentBatch).scalar_subquery())
    batches = db.execute(select(AgentBatch, obs).where(AgentBatch.exit_node_id == node.id)
                         .order_by(AgentBatch.received_at.desc()).limit(20)).all()
    return ExitNodeDetail(
        node=_node_out(db, node, health), heartbeats=[_hb_out(h) for h in hbs],
        batches=[BatchOut(received_at=b.received_at, window_start=b.window_start, window_end=b.window_end,
                          sample_count=b.sample_count, observations=int(n)) for b, n in batches],
        evaluated_at=now, health_max_age_seconds=int(quarantine.HEALTH_MAX_AGE.total_seconds()),
    )
