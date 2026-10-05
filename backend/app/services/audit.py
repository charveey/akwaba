"""Écriture dans audit_logs. L'appelant fait le commit : métier et audit partagent la transaction."""
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def record(
    db: Session, action: str, *, actor_admin_id, entity_type: str, entity_id,
    details: dict[str, Any] | None = None, ip: str | None = None,
) -> None:
    db.add(AuditLog(
        actor_admin_id=actor_admin_id, action=action, entity_type=entity_type,
        entity_id=str(entity_id), details=details, ip_address=ip,
    ))
