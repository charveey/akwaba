"""Enregistrement des heartbeats. Un heartbeat par cycle ; la purge viendra avec le scheduler."""
from datetime import timezone

from sqlalchemy.orm import Session

from app.models.infra import AgentHeartbeat
from app.schemas.agent_heartbeat import HeartbeatIn
from app.services.agent_auth import AgentContext


def record_heartbeat(db: Session, ctx: AgentContext, hb: HeartbeatIn) -> None:
    details = hb.details.model_dump(mode="json")
    details["generated_at"] = hb.generated_at.astimezone(timezone.utc).isoformat()
    db.add(AgentHeartbeat(
        exit_node_id=ctx.exit_node_id, agent_version=hb.agent_version, protocol_version=hb.schema_version,
        conntrack_ok=hb.conntrack_ok, status_ok=hb.status_ok, whois_ok=hb.whois_ok,
        bytes_available=hb.bytes_available, spool_depth=hb.spool_depth,
        conntrack_count=hb.conntrack_count, conntrack_max=hb.conntrack_max,
        last_error=hb.last_error, details=details,
    ))
    db.commit()
