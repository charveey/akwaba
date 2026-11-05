"""Dossiers de quarantaine : réconciliation automatique et décisions de l'administrateur.

Dry-run uniquement : les décisions écrites (WOULD_BLOCK / WOULD_UNBLOCK) ne sont jamais appliquées et la base
l'interdit (CHECK mode='dry_run', enforced=false). Aucune action réseau n'existe ici.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.domain import quarantine as q
from app.domain.clock import Clock
from app.domain.identity import Verdict, evaluate_identity
from app.domain.subscriptions import AccessStatus
from app.models.audit import AuditLog
from app.models.infra import (
    AgentHeartbeat, EnforcementDecision, ExitNodeIdentity, IdentityExemption, QuarantineRecord,
)
from app.models.system import JobRun
from app.schemas.identities import IdentityEvaluationOut
from app.services.audit import record as audit_record
from app.services.identities import evaluate_identities

logger = logging.getLogger("akwaba.reconcile")

JOB_NAME = "reconcile"
LOCK_KEY = 7_415_001                      # verrou consultatif PostgreSQL
HEALTH_MAX_AGE = timedelta(minutes=5)


@dataclass
class PlannedAction:
    login_name: str
    kind: str                              # OPEN | CHANGE | SKIP
    from_status: Optional[str]
    to_status: Optional[str]
    decision: Optional[str]
    reason: str


@dataclass
class ReconcileReport:
    evaluated: int = 0
    opened: int = 0
    changed: int = 0
    blocked: int = 0
    unblocked: int = 0
    authorized_auto: int = 0
    skipped_unhealthy: int = 0
    anomalies: Dict[str, int] = field(default_factory=dict)
    errors: int = 0
    last_error: Optional[str] = None
    planned: List[PlannedAction] = field(default_factory=list)

    def details(self) -> dict:
        """Compteurs seulement : jamais de login."""
        return {
            "evaluated": self.evaluated, "opened": self.opened, "changed": self.changed,
            "blocked": self.blocked, "unblocked": self.unblocked, "authorized_auto": self.authorized_auto,
            "skipped_unhealthy": self.skipped_unhealthy, "anomalies": dict(self.anomalies),
            "errors": self.errors, "last_error": self.last_error,
        }


# ------------------------------------------------------------------ lectures

def _status_verdict(item: IdentityEvaluationOut) -> Verdict:
    """Verdict de statut : activité forcée à vrai. L'identité existe, donc elle a été résolue."""
    accesses = [AccessStatus(item.access)] if item.access else []
    return evaluate_identity(
        resolved=True, active_confirmed=True, is_protected=item.protected,
        member_linked=item.member is not None, accesses=accesses,
    ).verdict


def _health(db: Session, now: datetime, max_age: timedelta) -> Dict[uuid.UUID, bool]:
    rows = db.execute(
        select(AgentHeartbeat.exit_node_id, AgentHeartbeat.received_at, AgentHeartbeat.conntrack_ok,
               AgentHeartbeat.whois_ok)
        .distinct(AgentHeartbeat.exit_node_id)
        .order_by(AgentHeartbeat.exit_node_id, AgentHeartbeat.received_at.desc(), AgentHeartbeat.id.desc())
    ).all()
    return {node: (now - at <= max_age and bool(conntrack) and bool(whois)) for node, at, conntrack, whois in rows}


def _last_decisions(db: Session) -> Dict[uuid.UUID, str]:
    rows = db.execute(
        select(EnforcementDecision.exit_node_identity_id, EnforcementDecision.decision)
        .distinct(EnforcementDecision.exit_node_identity_id)
        .order_by(EnforcementDecision.exit_node_identity_id, EnforcementDecision.decided_at.desc(),
                  EnforcementDecision.id.desc())
    ).all()
    return {identity: decision for identity, decision in rows}


def _last_decision(db: Session, identity_id: uuid.UUID) -> Optional[str]:
    return db.execute(
        select(EnforcementDecision.decision)
        .where(EnforcementDecision.exit_node_identity_id == identity_id)
        .order_by(EnforcementDecision.decided_at.desc(), EnforcementDecision.id.desc()).limit(1)
    ).scalar_one_or_none()


# ------------------------------------------------------------------ écritures

def _write_decision(db: Session, identity_id, record_id, decision: str, now: datetime, reason: str, context: dict) -> None:
    db.add(EnforcementDecision(
        exit_node_identity_id=identity_id, quarantine_record_id=record_id, decision=decision,
        mode="dry_run", enforced=False, decided_at=now, reason=reason[:255], context=context,
    ))


def _audit_auto(db: Session, action: str, record_id, details: dict) -> None:
    db.add(AuditLog(actor_label="reconciler", action=action, entity_type="quarantine_record",
                    entity_id=str(record_id), details=details))


def _apply(db: Session, item: IdentityEvaluationOut, record: Optional[QuarantineRecord], t: q.Transition,
           now: datetime, verdict: Verdict) -> None:
    old = record.status if record is not None else None
    if t.kind == q.OPEN:
        record = QuarantineRecord(exit_node_identity_id=item.id, status=t.new_status, started_at=now,
                                  expires_at=t.expires_at)
        db.add(record)
    else:
        record.status = t.new_status
        if t.resolved:
            record.resolved_at, record.resolution_note = now, t.note
    db.flush()
    if t.decision:
        _write_decision(db, item.id, record.id, t.decision, now, t.reason,
                        {"verdict": verdict.value, "from": old, "to": t.new_status})
    _audit_auto(db, "quarantine.opened" if t.kind == q.OPEN else "quarantine.changed", record.id, {
        "login_name": item.login_name, "from": old, "to": t.new_status, "decision": t.decision, "reason": t.reason})


def _count(report: ReconcileReport, t: q.Transition) -> None:
    if t.kind == q.OPEN:
        report.opened += 1
    elif t.kind == q.CHANGE:
        report.changed += 1
        if t.resolved and (t.note or "").startswith("auto"):
            report.authorized_auto += 1
    if t.decision == q.WOULD_BLOCK:
        report.blocked += 1
    elif t.decision == q.WOULD_UNBLOCK:
        report.unblocked += 1


# ---------------------------------------------------------------- réconciliation

def reconcile(db: Session, clock: Clock, *, apply: bool = True, window: timedelta = q.WINDOW,
              health_max_age: timedelta = HEALTH_MAX_AGE) -> ReconcileReport:
    now = clock.now()
    report = ReconcileReport()
    page = evaluate_identities(db, clock, verdict=None, active_now=None, q=None, limit=100_000, offset=0)

    records = {r.exit_node_identity_id: r for r in db.execute(
        select(QuarantineRecord).where(QuarantineRecord.status.in_(q.OPEN_STATUSES))).scalars()}
    exempt = set(db.execute(
        select(IdentityExemption.exit_node_identity_id).where(IdentityExemption.ended_at.is_(None))).scalars())
    dismissed = dict(db.execute(
        select(QuarantineRecord.exit_node_identity_id, func.max(QuarantineRecord.resolved_at))
        .where(QuarantineRecord.status == q.DISMISSED).group_by(QuarantineRecord.exit_node_identity_id)).all())
    last = _last_decisions(db)
    health = _health(db, now, health_max_age)

    for item in page.items:
        report.evaluated += 1
        record = records.get(item.id)
        open_record = q.OpenRecord(record.status, record.started_at, record.expires_at) if record else None
        verdict = _status_verdict(item)
        t = q.next_step(
            now=now, active_now=item.active_now, status_verdict=verdict, record=open_record,
            exempt=item.id in exempt, suppressed_until=q.suppressed_until(dismissed.get(item.id)), window=window)
        t, anomaly = q.guard(t, healthy=health.get(item.exit_node_id, False),
                             block_in_effect=last.get(item.id) == q.WOULD_BLOCK)
        if anomaly:
            report.anomalies[anomaly] = report.anomalies.get(anomaly, 0) + 1
        if t.kind == q.NONE:
            if t.reason == "agent_unhealthy":
                report.skipped_unhealthy += 1
                report.planned.append(PlannedAction(item.login_name, "SKIP", record.status if record else None,
                                                    None, None, t.reason))
            continue
        _count(report, t)
        report.planned.append(PlannedAction(item.login_name, t.kind, record.status if record else None,
                                            t.new_status, t.decision, t.reason))
        if not apply:
            continue
        try:
            with db.begin_nested():                 # une identité en échec n'arrête pas les autres
                _apply(db, item, record, t, now, verdict)
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            report.last_error = type(exc).__name__
            logger.error("réconciliation en échec pour une identité : %s", type(exc).__name__)
    if apply:
        db.commit()
    return report


def _finish(maker, run_id: int, clock: Clock, status: str, details: Optional[dict], error: Optional[str]) -> None:
    with maker() as db:
        run = db.get(JobRun, run_id)
        run.status, run.finished_at, run.details, run.error = status, clock.now(), details, error
        db.commit()


def run_job(maker, clock: Clock, *, apply: bool = True) -> Optional[ReconcileReport]:
    """Un traitement, sous verrou. None si un autre traitement est déjà en cours."""
    with maker() as probe:
        engine = probe.get_bind()
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    with conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar_one():
            return None
        try:
            if not apply:                              # --plan : aucune écriture, pas même job_runs
                with maker() as db:
                    return reconcile(db, clock, apply=False)
            with maker() as db:
                run = JobRun(job_name=JOB_NAME, status="RUNNING", started_at=clock.now())
                db.add(run)
                db.commit()
                run_id = run.id
            with maker() as db:
                try:
                    report = reconcile(db, clock, apply=True)
                except Exception as exc:
                    db.rollback()
                    _finish(maker, run_id, clock, "FAILED", None, type(exc).__name__)
                    raise
            _finish(maker, run_id, clock, "FAILED" if report.errors else "SUCCESS", report.details(),
                    report.last_error)
            return report
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})


# ------------------------------------------------------------ décisions humaines

def _login(db: Session, identity_id: uuid.UUID) -> str:
    return db.execute(select(ExitNodeIdentity.login_name).where(ExitNodeIdentity.id == identity_id)).scalar_one()


def resolve_record(db: Session, *, record_id: uuid.UUID, action, admin_id: uuid.UUID, now: datetime,
                   note: Optional[str] = None, ip: Optional[str] = None) -> QuarantineRecord:
    act = q.AdminAction(action)                        # ValueError si l'action est inconnue
    rec = db.execute(
        select(QuarantineRecord).where(QuarantineRecord.id == record_id).with_for_update()
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(404, "Dossier introuvable")
    try:
        t = q.admin_resolution(act, rec.status)
    except q.InvalidTransition:
        raise HTTPException(409, "Dossier déjà résolu")
    t, _ = q.guard(t, healthy=True,
                   block_in_effect=_last_decision(db, rec.exit_node_identity_id) == q.WOULD_BLOCK)
    old = rec.status
    rec.status = t.new_status
    rec.resolved_at, rec.resolved_by_admin_id, rec.resolution_note = now, admin_id, note
    db.flush()
    if act is q.AdminAction.AUTHORIZE:
        active = db.execute(select(IdentityExemption.id).where(
            IdentityExemption.exit_node_identity_id == rec.exit_node_identity_id,
            IdentityExemption.ended_at.is_(None))).first()
        if active is None:
            db.add(IdentityExemption(exit_node_identity_id=rec.exit_node_identity_id, quarantine_record_id=rec.id,
                                     granted_at=now, granted_by_admin_id=admin_id))
    if t.decision:
        _write_decision(db, rec.exit_node_identity_id, rec.id, t.decision, now, t.reason,
                        {"from": old, "to": rec.status, "by": "admin"})
    audit_record(db, "quarantine." + rec.status.lower(), actor_admin_id=admin_id, entity_type="quarantine_record",
                 entity_id=rec.id, ip=ip,
                 details={"login_name": _login(db, rec.exit_node_identity_id), "from": old, "to": rec.status,
                          "decision": t.decision})
    db.commit()
    return rec


def end_exemption(db: Session, *, exemption_id: uuid.UUID, admin_id: uuid.UUID, now: datetime,
                  ip: Optional[str] = None) -> IdentityExemption:
    ex = db.execute(
        select(IdentityExemption).where(IdentityExemption.id == exemption_id).with_for_update()
    ).scalar_one_or_none()
    if ex is None:
        raise HTTPException(404, "Exemption introuvable")
    if ex.ended_at is not None:
        raise HTTPException(409, "Exemption déjà retirée")
    ex.ended_at, ex.ended_by_admin_id = now, admin_id
    audit_record(db, "identity_exemption.ended", actor_admin_id=admin_id, entity_type="identity_exemption",
                 entity_id=ex.id, ip=ip, details={"login_name": _login(db, ex.exit_node_identity_id)})
    db.commit()
    return ex
