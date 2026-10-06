"""Abonnements, renouvellements, paiements. Chaque écriture et son audit partagent la transaction."""
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.config import Settings
from app.domain.clock import Clock
from app.domain.currency import eur_cents_to_xof, settlement_for
from app.domain.subscriptions import (
    AccessStatus, access_status, compute_expires_on, display_end, grace_end, renewal_start, renewal_status,
)
from app.models.billing import Payment, PaymentMethod, Subscription, SubscriptionPlan
from app.models.clients import Member
from app.schemas.billing import (
    PaymentCreate, PaymentInfo, PaymentMethodOut, PaymentOut, PlanOut, RenewIn, SubscriptionCreate,
    SubscriptionOut, VoidIn,
)
from app.services.audit import record
from app.services.auth import AuthContext


def _err(code: int, msg: str) -> HTTPException:
    return HTTPException(code, msg)


# ---------------------------------------------------------------- lecture

def _payment_out(p: Payment, method_code: str) -> PaymentOut:
    return PaymentOut(
        id=p.id, subscription_id=p.subscription_id, payment_method_code=method_code, amount=p.amount,
        currency=p.currency, settled_amount=p.settled_amount, settled_currency=p.settled_currency,
        fx_rate=p.fx_rate, paid_at=p.paid_at, status=p.status, reference=p.reference,
        voided_at=p.voided_at, void_reason=p.void_reason, created_at=p.created_at,
    )


def _views(db: Session, clock: Clock, subs: list[Subscription]) -> list[SubscriptionOut]:
    if not subs:
        return []
    ids = [s.id for s in subs]
    plans = {
        p.id: p for p in db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id.in_({s.plan_id for s in subs}))
        ).scalars()
    }
    with_successor = set(db.execute(
        select(Subscription.previous_subscription_id).where(Subscription.previous_subscription_id.in_(ids))
    ).scalars())
    payments: dict[uuid.UUID, list[PaymentOut]] = defaultdict(list)
    rows = db.execute(
        select(Payment, PaymentMethod.code)
        .join(PaymentMethod, PaymentMethod.id == Payment.payment_method_id)
        .where(Payment.subscription_id.in_(ids))
        .order_by(Payment.paid_at, Payment.id)
    ).all()
    for pay, code in rows:
        payments[pay.subscription_id].append(_payment_out(pay, code))

    today = clock.today()
    out = []
    for s in subs:
        plan = plans[s.plan_id]
        canceled = s.canceled_at is not None
        acc = access_status(
            today=today, starts_on=s.starts_on, expires_on=s.expires_on, is_unlimited=s.is_unlimited,
            grace_period_days=s.grace_period_days, payment_status=s.payment_status,
            canceled=canceled, suspended=s.suspended_at is not None,
        )
        ren = renewal_status(
            today=today, expires_on=s.expires_on, is_unlimited=s.is_unlimited,
            grace_period_days=s.grace_period_days, canceled=canceled, has_successor=s.id in with_successor,
        )
        out.append(SubscriptionOut(
            id=s.id, member_id=s.member_id, plan_code=plan.code, plan_name=plan.name,
            previous_subscription_id=s.previous_subscription_id, starts_on=s.starts_on,
            expires_on=s.expires_on, display_end=display_end(s.expires_on),
            grace_end=None if s.expires_on is None else grace_end(s.expires_on, s.grace_period_days),
            is_unlimited=s.is_unlimited, grace_period_days=s.grace_period_days,
            price_amount=s.price_amount, currency=s.currency, payment_status=s.payment_status,
            canceled_at=s.canceled_at, suspended_at=s.suspended_at,
            access_status=acc.value, renewal_status=ren.value, created_at=s.created_at,
            payments=payments[s.id],
        ))
    return out


def list_plans(db: Session) -> list[PlanOut]:
    plans = db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order, SubscriptionPlan.code)
    ).scalars()
    out = []
    for p in plans:
        xof = eur_cents_to_xof(p.price_amount) if p.currency == "EUR" else (p.price_amount if p.currency == "XOF" else None)
        out.append(PlanOut(
            id=p.id, code=p.code, name=p.name, price_amount=p.price_amount, currency=p.currency,
            price_xof=xof, duration_months=p.duration_months, is_unlimited=p.is_unlimited,
        ))
    return out


def list_payment_methods(db: Session) -> list[PaymentMethodOut]:
    rows = db.execute(
        select(PaymentMethod).where(PaymentMethod.is_active.is_(True)).order_by(PaymentMethod.name)
    ).scalars()
    return [PaymentMethodOut(id=m.id, code=m.code, name=m.name) for m in rows]


def list_subscriptions(
    db: Session, clock: Clock, *, member_id: uuid.UUID | None, limit: int, offset: int,
) -> tuple[list[SubscriptionOut], int]:
    stmt = select(Subscription)
    if member_id is not None:
        stmt = stmt.where(Subscription.member_id == member_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    subs = db.execute(
        stmt.order_by(Subscription.created_at.desc(), Subscription.id).limit(limit).offset(offset)
    ).scalars().all()
    return _views(db, clock, list(subs)), total


def get_subscription(db: Session, clock: Clock, sub_id: uuid.UUID) -> SubscriptionOut:
    sub = db.get(Subscription, sub_id)
    if sub is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Abonnement introuvable")
    return _views(db, clock, [sub])[0]


def list_payments(
    db: Session, *, subscription_id: uuid.UUID | None, member_id: uuid.UUID | None,
    pay_status: str | None, limit: int, offset: int,
) -> tuple[list[PaymentOut], int]:
    conds = []
    if subscription_id is not None:
        conds.append(Payment.subscription_id == subscription_id)
    if member_id is not None:
        conds.append(Subscription.member_id == member_id)
    if pay_status is not None:
        conds.append(Payment.status == pay_status)
    base = select(Payment.id).join(Subscription, Subscription.id == Payment.subscription_id).where(*conds)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(
        select(Payment, PaymentMethod.code)
        .join(PaymentMethod, PaymentMethod.id == Payment.payment_method_id)
        .join(Subscription, Subscription.id == Payment.subscription_id)
        .where(*conds)
        .order_by(Payment.paid_at.desc(), Payment.id).limit(limit).offset(offset)
    ).all()
    return [_payment_out(p, code) for p, code in rows], total


# ---------------------------------------------------------------- aides d'écriture

def _lock_subscription(db: Session, sub_id: uuid.UUID) -> Subscription:
    sub = db.execute(select(Subscription).where(Subscription.id == sub_id).with_for_update()).scalar_one_or_none()
    if sub is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Abonnement introuvable")
    return sub


def _has_successor(db: Session, sub_id: uuid.UUID) -> bool:
    return db.execute(
        select(Subscription.id).where(Subscription.previous_subscription_id == sub_id).limit(1)
    ).first() is not None


def _plan(db: Session, plan_id: uuid.UUID) -> SubscriptionPlan:
    plan = db.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Plan introuvable")
    if not plan.is_active:
        raise _err(status.HTTP_409_CONFLICT, "Plan inactif")
    if not plan.is_unlimited and plan.price_amount <= 0:
        raise _err(status.HTTP_409_CONFLICT, "Plan à prix nul non pris en charge")
    return plan


def _utc_date(dt: datetime):
    return dt.astimezone(timezone.utc).date()


def _resolve_paid_at(info: PaymentInfo, clock: Clock) -> datetime:
    now = clock.now()
    paid_at = info.paid_at or now
    if paid_at > now:
        raise _err(status.HTTP_422_UNPROCESSABLE_ENTITY, "La date de paiement ne peut pas être dans le futur")
    return paid_at


def _new_subscription(
    member_id: uuid.UUID, plan: SubscriptionPlan, starts_on, settings: Settings,
    previous_id: uuid.UUID | None, payment_status: str,
) -> Subscription:
    return Subscription(
        member_id=member_id, plan_id=plan.id, previous_subscription_id=previous_id, starts_on=starts_on,
        expires_on=compute_expires_on(starts_on, plan.duration_months), is_unlimited=plan.is_unlimited,
        grace_period_days=settings.default_grace_period_days, price_amount=plan.price_amount,
        currency=plan.currency, payment_status=payment_status,
    )


def _add_payment(
    db: Session, ctx: AuthContext, sub: Subscription, info: PaymentInfo, paid_at: datetime, ip: str | None,
) -> Payment:
    method = db.get(PaymentMethod, info.payment_method_id)
    if method is None or not method.is_active:
        raise _err(status.HTTP_422_UNPROCESSABLE_ENTITY, "Moyen de paiement introuvable ou inactif")
    try:
        settled, settled_cur, rate = settlement_for(sub.currency, sub.price_amount, info.settled_amount)
    except ValueError as exc:
        raise _err(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    pay = Payment(
        subscription_id=sub.id, payment_method_id=method.id, amount=sub.price_amount, currency=sub.currency,
        paid_at=paid_at, reference=info.reference, recorded_by_admin_id=ctx.admin.id,
        settled_amount=settled, settled_currency=settled_cur, fx_rate=rate,
    )
    db.add(pay)
    db.flush()
    record(db, "payment.recorded", actor_admin_id=ctx.admin.id, entity_type="payment", entity_id=pay.id,
           details={"subscription_id": str(sub.id), "amount": pay.amount, "currency": pay.currency,
                    "settled_amount": pay.settled_amount, "method": method.code}, ip=ip)
    return pay


def _ensure_no_open_subscription(db: Session, clock: Clock, member_id: uuid.UUID) -> None:
    succ = aliased(Subscription)
    heads = db.execute(
        select(Subscription).where(
            Subscription.member_id == member_id,
            ~select(succ.id).where(succ.previous_subscription_id == Subscription.id).exists(),
        )
    ).scalars().all()
    today = clock.today()
    for s in heads:
        st = access_status(
            today=today, starts_on=s.starts_on, expires_on=s.expires_on, is_unlimited=s.is_unlimited,
            grace_period_days=s.grace_period_days, payment_status=s.payment_status,
            canceled=s.canceled_at is not None, suspended=s.suspended_at is not None,
        )
        if st != AccessStatus.EXPIRED:
            raise _err(status.HTTP_409_CONFLICT, "Abonnement en cours : le renouveler ou l'annuler d'abord")


# ---------------------------------------------------------------- écritures

def create_subscription(
    db: Session, ctx: AuthContext, clock: Clock, settings: Settings, data: SubscriptionCreate, ip: str | None,
) -> SubscriptionOut:
    member = db.execute(select(Member).where(Member.id == data.member_id).with_for_update()).scalar_one_or_none()
    if member is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Membre introuvable")
    if member.archived_at is not None:
        raise _err(status.HTTP_409_CONFLICT, "Membre archivé")
    plan = _plan(db, data.plan_id)
    if plan.is_unlimited and data.payment is not None:
        raise _err(status.HTTP_422_UNPROCESSABLE_ENTITY, "Plan illimité : aucun paiement")
    _ensure_no_open_subscription(db, clock, member.id)

    paid_at = _resolve_paid_at(data.payment, clock) if data.payment else None
    starts_on = data.starts_on or (_utc_date(paid_at) if paid_at else clock.today())
    pay_status = "WAIVED" if plan.is_unlimited else ("PAID" if data.payment else "PENDING")
    sub = _new_subscription(member.id, plan, starts_on, settings, None, pay_status)
    db.add(sub)
    db.flush()
    record(db, "subscription.created", actor_admin_id=ctx.admin.id, entity_type="subscription", entity_id=sub.id,
           details={"member_id": str(member.id), "plan": plan.code, "starts_on": starts_on.isoformat(),
                    "expires_on": sub.expires_on.isoformat() if sub.expires_on else None}, ip=ip)
    if data.payment:
        _add_payment(db, ctx, sub, data.payment, paid_at, ip)
    db.commit()
    return _views(db, clock, [sub])[0]


def renew_subscription(
    db: Session, ctx: AuthContext, clock: Clock, settings: Settings, sub_id: uuid.UUID, data: RenewIn,
    ip: str | None,
) -> SubscriptionOut:
    sub = _lock_subscription(db, sub_id)
    member = db.get(Member, sub.member_id)
    if member is None or member.archived_at is not None:
        raise _err(status.HTTP_409_CONFLICT, "Membre archivé")
    if sub.is_unlimited:
        raise _err(status.HTTP_409_CONFLICT, "Abonnement illimité : aucun renouvellement")
    if sub.canceled_at is not None:
        raise _err(status.HTTP_409_CONFLICT, "Abonnement annulé : créer un nouvel abonnement")
    if sub.payment_status != "PAID":
        raise _err(status.HTTP_409_CONFLICT, "Abonnement non payé : enregistrer le paiement d'abord")
    if _has_successor(db, sub.id):
        raise _err(status.HTTP_409_CONFLICT, "Abonnement déjà renouvelé")
    plan = _plan(db, data.plan_id or sub.plan_id)
    if plan.is_unlimited:
        raise _err(status.HTTP_422_UNPROCESSABLE_ENTITY, "Un renouvellement ne peut pas viser un plan illimité")

    paid_at = _resolve_paid_at(data.payment, clock)
    assert sub.expires_on is not None
    start = renewal_start(sub.expires_on, _utc_date(paid_at), sub.grace_period_days, settings.renewal_in_grace_start)
    new = _new_subscription(sub.member_id, plan, start, settings, sub.id, "PAID")
    db.add(new)
    try:
        db.flush()  # l'index unique partiel tranche une éventuelle course
    except IntegrityError:
        db.rollback()
        raise _err(status.HTTP_409_CONFLICT, "Abonnement déjà renouvelé")
    record(db, "subscription.renewed", actor_admin_id=ctx.admin.id, entity_type="subscription", entity_id=new.id,
           details={"previous_subscription_id": str(sub.id), "plan": plan.code,
                    "starts_on": start.isoformat(), "expires_on": new.expires_on.isoformat()}, ip=ip)
    _add_payment(db, ctx, new, data.payment, paid_at, ip)
    db.commit()
    return _views(db, clock, [new])[0]


def cancel_subscription(
    db: Session, ctx: AuthContext, clock: Clock, sub_id: uuid.UUID, ip: str | None,
) -> SubscriptionOut:
    sub = _lock_subscription(db, sub_id)
    if sub.canceled_at is not None:
        raise _err(status.HTTP_409_CONFLICT, "Abonnement déjà annulé")
    if _has_successor(db, sub.id):
        raise _err(status.HTTP_409_CONFLICT, "Abonnement renouvelé : annuler la dernière ligne de la chaîne")
    sub.canceled_at = clock.now()
    record(db, "subscription.canceled", actor_admin_id=ctx.admin.id, entity_type="subscription",
           entity_id=sub.id, details={"member_id": str(sub.member_id)}, ip=ip)
    db.commit()
    return _views(db, clock, [sub])[0]


def record_payment(
    db: Session, ctx: AuthContext, clock: Clock, data: PaymentCreate, ip: str | None,
) -> PaymentOut:
    sub = _lock_subscription(db, data.subscription_id)
    if sub.is_unlimited:
        raise _err(status.HTTP_409_CONFLICT, "Abonnement illimité : aucun paiement")
    if sub.canceled_at is not None:
        raise _err(status.HTTP_409_CONFLICT, "Abonnement annulé")
    if sub.payment_status == "PAID":
        raise _err(status.HTTP_409_CONFLICT, "Abonnement déjà payé")
    paid_at = _resolve_paid_at(data, clock)
    pay = _add_payment(db, ctx, sub, data, paid_at, ip)
    sub.payment_status = "PAID"
    db.commit()
    code = db.execute(select(PaymentMethod.code).where(PaymentMethod.id == pay.payment_method_id)).scalar_one()
    return _payment_out(pay, code)


def void_payment(
    db: Session, ctx: AuthContext, clock: Clock, payment_id: uuid.UUID, data: VoidIn, ip: str | None,
) -> PaymentOut:
    pay = db.execute(select(Payment).where(Payment.id == payment_id).with_for_update()).scalar_one_or_none()
    if pay is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Paiement introuvable")
    if pay.status == "VOIDED":
        raise _err(status.HTTP_409_CONFLICT, "Paiement déjà annulé")
    sub = _lock_subscription(db, pay.subscription_id)
    pay.status = "VOIDED"
    pay.voided_at = clock.now()
    pay.void_reason = data.reason
    db.flush()
    if sub.payment_status == "PAID":
        others = db.execute(
            select(func.count()).select_from(Payment).where(
                Payment.subscription_id == sub.id, Payment.status == "CONFIRMED", Payment.id != pay.id,
            )
        ).scalar_one()
        if others == 0:
            sub.payment_status = "PENDING"
    record(db, "payment.voided", actor_admin_id=ctx.admin.id, entity_type="payment", entity_id=pay.id,
           details={"subscription_id": str(sub.id), "subscription_payment_status": sub.payment_status}, ip=ip)
    db.commit()
    code = db.execute(select(PaymentMethod.code).where(PaymentMethod.id == pay.payment_method_id)).scalar_one()
    return _payment_out(pay, code)
