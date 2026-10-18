"""Ingestion d'un lot : idempotence par batch_id, identités, pairs, observations, sessions."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.agent_protocol import ALLOWED_SKEW_SECONDS
from app.domain.clock import Clock
from app.models.infra import (
    AgentBatch, ExitNodeIdentity, ExitNodeObservation, ExitNodePeer, ExitNodeSession,
)
from app.schemas.agent import AgentIdentityIn, BatchIn
from app.services.agent_auth import AgentContext

logger = logging.getLogger("akwaba.agent")

SESSION_GAP = timedelta(minutes=10)
MAX_AGE = timedelta(days=30)
MAX_FUTURE = timedelta(seconds=ALLOWED_SKEW_SECONDS)


class IngestRejected(Exception):
    """Lot définitivement refusé (422) : l'agent doit l'abandonner."""


class BatchConflict(Exception):
    """Ce batch_id appartient à un autre exit node (409)."""


@dataclass
class IngestResult:
    status: str          # "accepted" | "duplicate"
    observations: int


def _upsert_identity(db: Session, node_id, ident: AgentIdentityIn, at: datetime):
    stmt = pg_insert(ExitNodeIdentity).values(
        exit_node_id=node_id, login_name=ident.login_name, tailscale_user_id=ident.tailscale_user_id,
        is_sharee=ident.is_sharee, first_seen_at=at, last_seen_at=at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["exit_node_id", "login_name"],
        set_={
            "last_seen_at": func.greatest(ExitNodeIdentity.last_seen_at, stmt.excluded.last_seen_at),
            "first_seen_at": func.least(ExitNodeIdentity.first_seen_at, stmt.excluded.first_seen_at),
            "is_sharee": stmt.excluded.is_sharee,
            "tailscale_user_id": func.coalesce(stmt.excluded.tailscale_user_id, ExitNodeIdentity.tailscale_user_id),
        },
    ).returning(ExitNodeIdentity.id)
    return db.execute(stmt).scalar_one()


def _upsert_peer(db: Session, node_id, identity_id, ident: AgentIdentityIn, ip: str, at: datetime):
    stmt = pg_insert(ExitNodePeer).values(
        exit_node_id=node_id, identity_id=identity_id, stable_id=ident.stable_id, tailscale_ip=ip,
        is_sharee=ident.is_sharee, first_seen_at=at, last_seen_at=at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["exit_node_id", "stable_id"],
        set_={
            "identity_id": stmt.excluded.identity_id,
            "tailscale_ip": stmt.excluded.tailscale_ip,
            "is_sharee": stmt.excluded.is_sharee,
            "last_seen_at": func.greatest(ExitNodePeer.last_seen_at, stmt.excluded.last_seen_at),
            "first_seen_at": func.least(ExitNodePeer.first_seen_at, stmt.excluded.first_seen_at),
        },
    ).returning(ExitNodePeer.id)
    return db.execute(stmt).scalar_one()


def _touch_session(db: Session, node_id, identity_id, ip: str, at: datetime, new_flows, active_flows: int) -> None:
    sess = db.execute(
        select(ExitNodeSession).where(
            ExitNodeSession.identity_id == identity_id,
            ExitNodeSession.tailscale_ip == ip,
            ExitNodeSession.closed_at.is_(None),
        ).with_for_update()
    ).scalar_one_or_none()
    if sess is not None and at < sess.first_seen_at - SESSION_GAP:
        return                                    # lot ancien arrivé tard : l'observation est gardée, pas la session
    if sess is not None and at - sess.last_seen_at > SESSION_GAP:
        sess.closed_at = sess.last_seen_at
        db.flush()                                # libère l'index « une seule session ouverte »
        sess = None
    if sess is None:
        db.add(ExitNodeSession(
            exit_node_id=node_id, identity_id=identity_id, tailscale_ip=ip, first_seen_at=at,
            last_seen_at=at, sample_count=1, new_flows_total=new_flows or 0, peak_active_flows=active_flows,
        ))
        return
    sess.first_seen_at = min(sess.first_seen_at, at)
    sess.last_seen_at = max(sess.last_seen_at, at)
    sess.sample_count += 1
    sess.new_flows_total += new_flows or 0
    sess.peak_active_flows = max(sess.peak_active_flows, active_flows)


def ingest_batch(db: Session, ctx: AgentContext, clock: Clock, batch: BatchIn) -> IngestResult:
    now = clock.now()
    at = batch.sampled_at.astimezone(timezone.utc)
    if at > now + MAX_FUTURE:
        raise IngestRejected("sampled_at est dans le futur")
    if at < now - MAX_AGE:
        raise IngestRejected("sampled_at est trop ancien")
    node_id = ctx.exit_node_id

    inserted = db.execute(
        pg_insert(AgentBatch).values(
            batch_id=batch.batch_id, exit_node_id=node_id, protocol_version=batch.schema_version,
            window_start=at - timedelta(seconds=batch.window_seconds), window_end=at,
            sample_count=len(batch.observations),
        ).on_conflict_do_nothing(index_elements=["batch_id"]).returning(AgentBatch.batch_id)
    ).first()
    if inserted is None:
        owner = db.execute(select(AgentBatch.exit_node_id).where(AgentBatch.batch_id == batch.batch_id)).scalar_one()
        db.rollback()
        if owner != node_id:
            raise BatchConflict()
        return IngestResult("duplicate", len(batch.observations))

    identities: dict = {}
    peers: dict = {}
    resolved = 0
    for o in batch.observations:
        identity_id = peer_id = None
        if o.identity is not None:
            resolved += 1
            login = o.identity.login_name
            if login not in identities:
                identities[login] = _upsert_identity(db, node_id, o.identity, at)
            identity_id = identities[login]
            if o.identity.stable_id:
                if o.identity.stable_id not in peers:
                    peers[o.identity.stable_id] = _upsert_peer(db, node_id, identity_id, o.identity, o.src_ip, at)
                peer_id = peers[o.identity.stable_id]
        db.add(ExitNodeObservation(
            batch_id=batch.batch_id, exit_node_id=node_id, identity_id=identity_id, peer_id=peer_id,
            tailscale_ip=o.src_ip, observed_at=at, window_seconds=batch.window_seconds,
            resolution_status=o.identity_status, active_flows=o.recent_flows, new_flows=o.new_flows,
            replied_flows=o.recent_replied_flows, total_flows=o.total_flows,
            unknown_age_flows=o.unknown_age_flows, consecutive_active_windows=o.consecutive_active_windows,
            active_confirmed=o.active_confirmed, identity_technical_error=o.identity_technical_error,
        ))
        if identity_id is not None:
            _touch_session(db, node_id, identity_id, o.src_ip, at, o.new_flows, o.recent_flows)

    db.execute(
        update(ExitNodeSession)
        .where(ExitNodeSession.exit_node_id == node_id, ExitNodeSession.closed_at.is_(None),
               ExitNodeSession.last_seen_at < at - SESSION_GAP)
        .values(closed_at=ExitNodeSession.last_seen_at)
    )
    db.commit()
    logger.info("lot accepté observations=%d résolues=%d", len(batch.observations), resolved)
    return IngestResult("accepted", len(batch.observations))
