"""Relances WhatsApp. Écritures et audit dans la même transaction. Aucun texte ni numéro dans l'audit."""
import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.clock import Clock
from app.domain.reminders import (
    OFFSETS, due_date, is_eligible, message_text, whatsapp_link,
)
from app.models.billing import Subscription, SubscriptionPlan
from app.models.clients import Member
from app.models.reminders import Reminder
from app.schemas.reminders import GenerateResult, ReminderLink, ReminderOut
from app.services.audit import record
from app.services.auth import AuthContext


def _err(code: int, msg: str) -> HTTPException:
    return HTTPException(code, msg)


def _renewed_ids(db: Session, sub_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not sub_ids:
        return set()
    return set(db.execute(
        select(Subscription.previous_subscription_id).where(Subscription.previous_subscription_id.in_(sub_ids))
    ).scalars())


def _views(db: Session, rows: list[tuple[Reminder, str]]) -> list[ReminderOut]:
    renewed = _renewed_ids(db, list({r.subscription_id for r, _ in rows}))
    out = []
    for r, name in rows:
        effective = r.status
        if r.status in ("PENDING", "OPENED") and r.subscription_id in renewed:
            effective = "CANCELED"
        out.append(ReminderOut(
            id=r.id, subscription_id=r.subscription_id, member_id=r.member_id, member_name=name,
            offset_days=r.offset_days, due_date=r.due_date, message=r.message, status=effective,
            opened_at=r.opened_at, sent_at=r.sent_at, created_at=r.created_at,
        ))
    return out


def _one(db: Session, reminder_id: uuid.UUID, *, lock: bool = False) -> tuple[Reminder, str]:
    stmt = select(Reminder, Member.full_name).join(Member, Member.id == Reminder.member_id).where(
        Reminder.id == reminder_id)
    if lock:
        stmt = stmt.with_for_update(of=Reminder)
    row = db.execute(stmt).first()
    if row is None:
        raise _err(404, "Relance introuvable")
    return row[0], row[1]


def list_reminders(
    db: Session, *, rem_status: str | None, due_on: date | None, member_id: uuid.UUID | None,
    limit: int, offset: int,
) -> tuple[list[ReminderOut], int]:
    conds = []
    if due_on is not None:
        conds.append(Reminder.due_date == due_on)
    if member_id is not None:
        conds.append(Reminder.member_id == member_id)
    base = select(Reminder, Member.full_name).join(Member, Member.id == Reminder.member_id).where(*conds)
    rows = db.execute(base.order_by(Reminder.due_date.desc(), Reminder.created_at.desc(), Reminder.id)).all()
    views = _views(db, [(r, n) for r, n in rows])
    if rem_status is not None:
        views = [v for v in views if v.status == rem_status]  # statut effectif
    total = len(views)
    return views[offset:offset + limit], total


def generate(db: Session, ctx: AuthContext, clock: Clock, ip: str | None) -> GenerateResult:
    today = clock.today()
    # Candidates : dernières lignes de chaîne, limitées, payées, non illimitées.
    succ = select(Subscription.previous_subscription_id).where(Subscription.previous_subscription_id.is_not(None))
    candidates = db.execute(
        select(Subscription, Member, SubscriptionPlan.duration_months)
        .join(Member, Member.id == Subscription.member_id)
        .join(SubscriptionPlan, SubscriptionPlan.id == Subscription.plan_id)
        .where(
            Subscription.is_unlimited.is_(False),
            Subscription.payment_status == "PAID",
            Subscription.canceled_at.is_(None),
            Subscription.suspended_at.is_(None),
            Subscription.expires_on.is_not(None),
            Subscription.id.not_in(succ),
        )
    ).all()
    created: list[tuple[Reminder, str]] = []
    skipped = 0
    for sub, member, duration in candidates:
        if duration is None:
            continue
        if not is_eligible(
            is_unlimited=sub.is_unlimited, payment_status=sub.payment_status,
            canceled=sub.canceled_at is not None, suspended=sub.suspended_at is not None,
            has_successor=False, member_archived=member.archived_at is not None, phone_e164=member.phone_e164,
        ):
            continue
        assert sub.expires_on is not None
        for offset in OFFSETS:
            if due_date(sub.expires_on, offset) != today:
                continue
            exists = db.execute(select(Reminder.id).where(
                Reminder.subscription_id == sub.id, Reminder.offset_days == offset)).first()
            if exists:
                skipped += 1
                continue
            rem = Reminder(
                subscription_id=sub.id, member_id=member.id, offset_days=offset, due_date=today,
                phone_e164=member.phone_e164,
                message=message_text(offset, duration, sub.expires_on, today),
            )
            db.add(rem)
            db.flush()
            created.append((rem, member.full_name))
    if created:
        record(db, "reminders.generated", actor_admin_id=ctx.admin.id, entity_type="reminder_batch",
               entity_id=today.isoformat(), details={"created": len(created), "date": today.isoformat()}, ip=ip)
    db.commit()
    return GenerateResult(today=today, created=len(created), skipped_existing=skipped,
                          reminders=_views(db, created))


def open_reminder(db: Session, ctx: AuthContext, clock: Clock, reminder_id: uuid.UUID, ip: str | None) -> ReminderLink:
    rem, name = _one(db, reminder_id, lock=True)
    view = _views(db, [(rem, name)])[0]
    if view.status == "CANCELED":
        raise _err(409, "Relance annulée : l'abonnement a été renouvelé")
    if rem.status == "SENT":
        raise _err(409, "Relance déjà envoyée")
    if rem.status == "PENDING":
        rem.status, rem.opened_at = "OPENED", clock.now()
        record(db, "reminder.opened", actor_admin_id=ctx.admin.id, entity_type="reminder",
               entity_id=rem.id, details={"offset_days": rem.offset_days}, ip=ip)
        db.commit()
        view = _views(db, [(rem, name)])[0]
    return ReminderLink(reminder=view, whatsapp_url=whatsapp_link(rem.phone_e164, rem.message))


def mark_sent(db: Session, ctx: AuthContext, clock: Clock, reminder_id: uuid.UUID, ip: str | None) -> ReminderOut:
    rem, name = _one(db, reminder_id, lock=True)
    if rem.status == "SENT":
        raise _err(409, "Relance déjà marquée comme envoyée")
    if _views(db, [(rem, name)])[0].status == "CANCELED":
        raise _err(409, "Relance annulée : l'abonnement a été renouvelé")
    now = clock.now()
    rem.status = "SENT"
    rem.sent_at = now
    if rem.opened_at is None:
        rem.opened_at = now
    record(db, "reminder.sent", actor_admin_id=ctx.admin.id, entity_type="reminder",
           entity_id=rem.id, details={"offset_days": rem.offset_days}, ip=ip)
    db.commit()
    return _views(db, [(rem, name)])[0]
