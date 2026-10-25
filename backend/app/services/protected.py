"""Identités protégées. Seule la source MANUAL est modifiable ici ; retrait par removed_at, jamais de DELETE."""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.infra import ProtectedIdentity
from app.schemas.protected import ProtectedOut
from app.services.audit import record


def _out(row: ProtectedIdentity) -> ProtectedOut:
    return ProtectedOut(
        id=row.id, login_name=row.login_name, source=row.source, reason=row.reason,
        created_at=row.created_at, removed_at=row.removed_at, active=row.removed_at is None,
    )


def _audit(db: Session, action: str, row: ProtectedIdentity, admin_id: Optional[uuid.UUID],
           label: str, ip: Optional[str]) -> None:
    # Le login est l'objet même de l'action (comme pour les liaisons d'identités). Jamais le motif libre.
    details = {"login_name": row.login_name, "source": row.source}
    if admin_id is not None:
        record(db, action, actor_admin_id=admin_id, entity_type="protected_identity",
               entity_id=row.id, details=details, ip=ip)
    else:
        db.add(AuditLog(actor_label=label, action=action, entity_type="protected_identity",
                        entity_id=str(row.id), details=details))


def list_protected(db: Session, *, include_removed: bool) -> List[ProtectedOut]:
    stmt = select(ProtectedIdentity)
    if not include_removed:
        stmt = stmt.where(ProtectedIdentity.removed_at.is_(None))
    rows = db.execute(stmt.order_by(ProtectedIdentity.created_at, ProtectedIdentity.login_name)).scalars()
    return [_out(r) for r in rows]


def add_protected(db: Session, *, login: str, reason: Optional[str], admin_id: Optional[uuid.UUID],
                  ip: Optional[str] = None, label: str = "api") -> ProtectedOut:
    taken = db.execute(
        select(ProtectedIdentity.id).where(
            ProtectedIdentity.login_name == login, ProtectedIdentity.source == "MANUAL",
            ProtectedIdentity.removed_at.is_(None))
    ).first()
    if taken is not None:
        raise HTTPException(409, "Cette identité est déjà protégée")
    row = ProtectedIdentity(login_name=login, source="MANUAL", reason=reason, created_by_admin_id=admin_id)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Cette identité est déjà protégée")
    _audit(db, "protected_identity.added", row, admin_id, label, ip)
    db.commit()
    return _out(row)


def remove_protected(db: Session, *, protected_id: uuid.UUID, now: datetime,
                     admin_id: Optional[uuid.UUID], ip: Optional[str] = None, label: str = "api") -> ProtectedOut:
    row = db.execute(
        select(ProtectedIdentity).where(ProtectedIdentity.id == protected_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Identité protégée introuvable")
    if row.removed_at is not None:
        raise HTTPException(409, "Protection déjà retirée")
    if row.source != "MANUAL":
        raise HTTPException(409, "Protection automatique : non modifiable manuellement")
    row.removed_at = now
    _audit(db, "protected_identity.removed", row, admin_id, label, ip)
    db.commit()
    return _out(row)
