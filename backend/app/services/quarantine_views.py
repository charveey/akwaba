"""Lectures des dossiers, décisions et exemptions. Aucune écriture."""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import quarantine as qd
from app.domain.clock import Clock, SystemClock
from app.models.admin import AdminUser
from app.models.infra import EnforcementDecision, ExitNodeIdentity, IdentityExemption, QuarantineRecord
from app.schemas.quarantine import (
    DecisionOut, DecisionPage, ExemptionOut, ExemptionPage, QuarantineDetail, QuarantineOut, QuarantinePage,
)


def _like(text: str) -> str:
    return "%" + text.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _check_range(date_from: Optional[date], date_to: Optional[date]) -> None:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from doit précéder date_to")


def _exempt_ids(db: Session, identity_ids: List[uuid.UUID]) -> set:
    if not identity_ids:
        return set()
    return set(db.execute(
        select(IdentityExemption.exit_node_identity_id).where(
            IdentityExemption.ended_at.is_(None), IdentityExemption.exit_node_identity_id.in_(identity_ids))
    ).scalars())


def _record_out(rec: QuarantineRecord, login: str, exempt: bool) -> QuarantineOut:
    return QuarantineOut(
        id=rec.id, exit_node_identity_id=rec.exit_node_identity_id, login_name=login, status=rec.status,
        started_at=rec.started_at, expires_at=rec.expires_at, resolved_at=rec.resolved_at,
        resolved_by_admin_id=rec.resolved_by_admin_id, resolution_note=rec.resolution_note,
        simulated_block=rec.status in qd.BLOCKED_STATUSES, exempt=exempt,
    )


def _decision_out(d: EnforcementDecision, login: str) -> DecisionOut:
    return DecisionOut(
        id=str(d.id), quarantine_record_id=d.quarantine_record_id, exit_node_identity_id=d.exit_node_identity_id,
        login_name=login, decision=d.decision, mode=d.mode, enforced=d.enforced, decided_at=d.decided_at,
        reason=d.reason,
    )


def _admin_emails(db: Session, ids) -> Dict:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict(db.execute(select(AdminUser.id, AdminUser.email).where(AdminUser.id.in_(ids))).all())


def _enrich_records(db: Session, clock: Clock, items: List[QuarantineOut]) -> List[QuarantineOut]:
    """Verdict actuel, dernière activité, temps restant et non-réouverture — calculés ici, à `now`."""
    from app.services.identities import evaluate_identities   # import local : pas de cycle

    if not items:
        return items
    now = clock.now()
    evaluations = {e.id: e for e in evaluate_identities(
        db, clock, verdict=None, active_now=None, q=None, limit=100_000, offset=0).items}
    emails = _admin_emails(db, [i.resolved_by_admin_id for i in items])
    out = []
    for item in items:
        ev = evaluations.get(item.exit_node_identity_id)
        extra = {
            "current_verdict": ev.verdict if ev else None,
            "current_reason": ev.reason if ev else None,
            "last_confirmed_activity_at": ev.last_confirmed_activity_at if ev else None,
            "resolved_by_email": emails.get(item.resolved_by_admin_id),
        }
        if item.status == qd.PENDING:
            extra["remaining_seconds"] = max(0, int((item.expires_at - now).total_seconds()))
        if item.status == qd.DISMISSED and item.resolved_at is not None:
            until = qd.suppressed_until(item.resolved_at)
            extra["suppressed_until"] = until
            extra["reopen_possible"] = now >= until
        out.append(item.model_copy(update=extra))
    return out


def list_records(db: Session, *, status: Optional[str], q: Optional[str], open_only: bool,
                 limit: int, offset: int, clock: Optional[Clock] = None,
                 identity_id: Optional[uuid.UUID] = None) -> QuarantinePage:
    clock = clock or SystemClock()
    scope = []
    if identity_id:
        scope.append(QuarantineRecord.exit_node_identity_id == identity_id)
    if q:
        scope.append(ExitNodeIdentity.login_name.ilike(_like(q), escape="\\"))
    join = ExitNodeIdentity.id == QuarantineRecord.exit_node_identity_id
    counts: Dict[str, int] = dict(db.execute(
        select(QuarantineRecord.status, func.count()).join(ExitNodeIdentity, join).where(*scope)
        .group_by(QuarantineRecord.status)).all())
    shown = list(scope)
    if status:
        shown.append(QuarantineRecord.status == status)
    if open_only:
        shown.append(QuarantineRecord.status.in_(qd.OPEN_STATUSES))
    total = db.execute(
        select(func.count()).select_from(QuarantineRecord).join(ExitNodeIdentity, join).where(*shown)).scalar_one()
    rows = db.execute(
        select(QuarantineRecord, ExitNodeIdentity.login_name).join(ExitNodeIdentity, join).where(*shown)
        .order_by(QuarantineRecord.started_at.desc(), QuarantineRecord.id).limit(limit).offset(offset)).all()
    exempt = _exempt_ids(db, [r.exit_node_identity_id for r, _ in rows])
    items = [_record_out(r, login, r.exit_node_identity_id in exempt) for r, login in rows]
    return QuarantinePage(items=_enrich_records(db, clock, items), total=int(total), counts=counts,
                          limit=limit, offset=offset, evaluated_at=clock.now())


def get_record(db: Session, record_id: uuid.UUID, clock: Optional[Clock] = None) -> QuarantineDetail:
    clock = clock or SystemClock()
    row = db.execute(
        select(QuarantineRecord, ExitNodeIdentity.login_name)
        .join(ExitNodeIdentity, ExitNodeIdentity.id == QuarantineRecord.exit_node_identity_id)
        .where(QuarantineRecord.id == record_id)).first()
    if row is None:
        raise HTTPException(404, "Dossier introuvable")
    rec, login = row
    decisions = db.execute(
        select(EnforcementDecision).where(EnforcementDecision.quarantine_record_id == record_id)
        .order_by(EnforcementDecision.decided_at.desc(), EnforcementDecision.id.desc())).scalars().all()
    exempt = rec.exit_node_identity_id in _exempt_ids(db, [rec.exit_node_identity_id])
    return QuarantineDetail(record=_enrich_records(db, clock, [_record_out(rec, login, exempt)])[0],
                            decisions=[_decision_out(d, login) for d in decisions], evaluated_at=clock.now())


def list_decisions(db: Session, *, decision: Optional[str], q: Optional[str], date_from: Optional[date],
                   date_to: Optional[date], limit: int, offset: int, identity_id: Optional[uuid.UUID] = None,
                   record_id: Optional[uuid.UUID] = None) -> DecisionPage:
    _check_range(date_from, date_to)
    conds = []
    if identity_id:
        conds.append(EnforcementDecision.exit_node_identity_id == identity_id)
    if record_id:
        conds.append(EnforcementDecision.quarantine_record_id == record_id)
    if decision:
        conds.append(EnforcementDecision.decision == decision)
    if q:
        conds.append(ExitNodeIdentity.login_name.ilike(_like(q), escape="\\"))
    if date_from:
        conds.append(EnforcementDecision.decided_at >= _day_start(date_from))
    if date_to:
        conds.append(EnforcementDecision.decided_at < _day_start(date_to) + timedelta(days=1))
    join = ExitNodeIdentity.id == EnforcementDecision.exit_node_identity_id
    total = db.execute(
        select(func.count()).select_from(EnforcementDecision).join(ExitNodeIdentity, join).where(*conds)).scalar_one()
    rows = db.execute(
        select(EnforcementDecision, ExitNodeIdentity.login_name).join(ExitNodeIdentity, join).where(*conds)
        .order_by(EnforcementDecision.decided_at.desc(), EnforcementDecision.id.desc())
        .limit(limit).offset(offset)).all()
    return DecisionPage(items=[_decision_out(d, login) for d, login in rows], total=int(total),
                        limit=limit, offset=offset)


def _exemption_out(ex: IdentityExemption, login: str) -> ExemptionOut:
    return ExemptionOut(
        id=ex.id, exit_node_identity_id=ex.exit_node_identity_id, login_name=login,
        quarantine_record_id=ex.quarantine_record_id, granted_at=ex.granted_at,
        granted_by_admin_id=ex.granted_by_admin_id, ended_at=ex.ended_at, ended_by_admin_id=ex.ended_by_admin_id,
        active=ex.ended_at is None,
    )


def _enrich_exemptions(db: Session, items: List[ExemptionOut]) -> List[ExemptionOut]:
    if not items:
        return items
    notes = dict(db.execute(select(QuarantineRecord.id, QuarantineRecord.resolution_note).where(
        QuarantineRecord.id.in_({i.quarantine_record_id for i in items if i.quarantine_record_id}))).all())
    emails = _admin_emails(db, [i.granted_by_admin_id for i in items] + [i.ended_by_admin_id for i in items])
    return [i.model_copy(update={
        "reason": notes.get(i.quarantine_record_id), "granted_by_email": emails.get(i.granted_by_admin_id),
        "ended_by_email": emails.get(i.ended_by_admin_id)}) for i in items]


def get_exemption(db: Session, exemption_id: uuid.UUID) -> ExemptionOut:
    row = db.execute(
        select(IdentityExemption, ExitNodeIdentity.login_name)
        .join(ExitNodeIdentity, ExitNodeIdentity.id == IdentityExemption.exit_node_identity_id)
        .where(IdentityExemption.id == exemption_id)).first()
    if row is None:
        raise HTTPException(404, "Exemption introuvable")
    return _enrich_exemptions(db, [_exemption_out(*row)])[0]


def list_exemptions(db: Session, *, active: Optional[bool], q: Optional[str], limit: int, offset: int,
                    identity_id: Optional[uuid.UUID] = None) -> ExemptionPage:
    conds = []
    if identity_id:
        conds.append(IdentityExemption.exit_node_identity_id == identity_id)
    if active is True:
        conds.append(IdentityExemption.ended_at.is_(None))
    elif active is False:
        conds.append(IdentityExemption.ended_at.is_not(None))
    if q:
        conds.append(ExitNodeIdentity.login_name.ilike(_like(q), escape="\\"))
    join = ExitNodeIdentity.id == IdentityExemption.exit_node_identity_id
    total = db.execute(
        select(func.count()).select_from(IdentityExemption).join(ExitNodeIdentity, join).where(*conds)).scalar_one()
    rows = db.execute(
        select(IdentityExemption, ExitNodeIdentity.login_name).join(ExitNodeIdentity, join).where(*conds)
        .order_by(IdentityExemption.granted_at.desc(), IdentityExemption.id).limit(limit).offset(offset)).all()
    return ExemptionPage(items=_enrich_exemptions(db, [_exemption_out(ex, login) for ex, login in rows]),
                         total=int(total), limit=limit, offset=offset)
