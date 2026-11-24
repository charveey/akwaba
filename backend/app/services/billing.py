"""Abonnements, renouvellements, paiements. Chaque écriture et son audit partagent la transaction."""
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.config import Settings
from app.domain.clock import Clock
from app.domain.clock import SystemClock as _SystemClock
from app.domain.currency import eur_cents_to_xof, settlement_for
from app.domain.subscriptions import (
    AccessStatus, access_status, compute_expires_on, display_end, grace_end, payment_kind, plan_changed,
    renewal_start, renewal_status,
)
from app.models.billing import Payment, PaymentMethod, Subscription, SubscriptionPlan
from app.models.clients import Member
from app.schemas.billing import (
    PaymentCreate, PaymentInfo, PaymentMethodOut, PaymentOut, PaymentTotals, PlanOut, RenewIn,
    SubscriptionCreate, SubscriptionOut, VoidIn,
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

    predecessors = _predecessors(db, subs)
    names = dict(db.execute(
        select(Member.id, Member.full_name).where(Member.id.in_({s.member_id for s in subs}))
    ).all())

    today = clock.today()
    out = []
    for s in subs:
        plan = plans[s.plan_id]
        pred = predecessors.get(s.id)
        confirmed = [p for p in payments[s.id] if p.status == "CONFIRMED"]
        kind = payment_kind(
            payment_status=s.payment_status,
            has_predecessor=pred is not None,
            predecessor_expires_on=pred.expires_on if pred is not None else None,
            paid_on=_utc_date(min(p.paid_at for p in confirmed)) if confirmed else None,
        )
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
            member_name=names.get(s.member_id), is_current=s.id not in with_successor,
            payment_kind=kind.value,
            plan_changed=plan_changed(pred.plan_id if pred is not None else None, s.plan_id),
            starts_in_future=today < s.starts_on,
        ))
    return out


def subscription_views(db: Session, clock: Clock, subs: list[Subscription]) -> list[SubscriptionOut]:
    """Vues calculées (accès, renouvellement, statut métier) — réutilisées par la liste des membres."""
    return _views(db, clock, subs)


def _predecessors(db: Session, subs: list[Subscription]) -> dict[uuid.UUID, Subscription]:
    """Abonnement payé antérieur de chaque abonnement (Phase 11b).

    - renouvellement : l'abonnement précédent de la chaîne ;
    - sinon : le plus récent abonnement PAID/WAIVED du même membre créé avant lui (réabonnement).
    Aucun prédécesseur = premier abonnement payé du membre.
    """
    member_ids = {s.member_id for s in subs}
    history = db.execute(
        select(Subscription).where(Subscription.member_id.in_(member_ids))
        .order_by(Subscription.created_at, Subscription.id)
    ).scalars().all()
    by_id = {h.id: h for h in history}
    result: dict[uuid.UUID, Subscription] = {}
    for s in subs:
        if s.previous_subscription_id is not None:
            prev = by_id.get(s.previous_subscription_id)
            if prev is not None:
                result[s.id] = prev
            continue
        earlier = [
            h for h in history
            if h.member_id == s.member_id and h.id != s.id
            and h.payment_status in ("PAID", "WAIVED")
            and (h.created_at, str(h.id)) < (s.created_at, str(s.id))
        ]
        if earlier:
            result[s.id] = earlier[-1]
    return result


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


SUBSCRIPTION_SORTS = {
    "-created": (Subscription.created_at.desc(),),
    "created": (Subscription.created_at.asc(),),
    "expiry": (Subscription.expires_on.asc().nulls_last(),),
    "-expiry": (Subscription.expires_on.desc().nulls_last(),),
    "member": (Member.full_name.asc(),),
    "-member": (Member.full_name.desc(),),
}


def list_subscriptions(
    db: Session, clock: Clock, *, member_id: uuid.UUID | None, limit: int, offset: int,
    current_only: bool = False, payment_status: str | None = None, plan_code: str | None = None,
    expiring_within_days: int | None = None, access: str | None = None, renewal: str | None = None,
    kind: str | None = None, sort: str = "-created",
) -> tuple[list[SubscriptionOut], int]:
    """Filtres SQL (simples) puis filtres calculés (accès, renouvellement, statut métier) en mémoire,
    TOUJOURS avant la pagination. Tri secondaire déterministe : identifiant."""
    stmt = select(Subscription).join(Member, Member.id == Subscription.member_id)
    if member_id is not None:
        stmt = stmt.where(Subscription.member_id == member_id)
    if current_only:
        succ = aliased(Subscription)
        stmt = stmt.where(~select(succ.id).where(succ.previous_subscription_id == Subscription.id).exists())
    if payment_status is not None:
        stmt = stmt.where(Subscription.payment_status == payment_status)
    if plan_code is not None:
        stmt = stmt.join(SubscriptionPlan, SubscriptionPlan.id == Subscription.plan_id).where(
            SubscriptionPlan.code == plan_code)
    if expiring_within_days is not None:
        today = clock.today()
        stmt = stmt.where(
            Subscription.expires_on.is_not(None),
            Subscription.expires_on > today,
            Subscription.expires_on <= today + timedelta(days=expiring_within_days),
        )
    stmt = stmt.order_by(*SUBSCRIPTION_SORTS[sort], Subscription.id)

    if access is None and renewal is None and kind is None:
        total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
        subs = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
        return _views(db, clock, list(subs)), total

    views = _views(db, clock, list(db.execute(stmt).scalars().all()))
    views = [
        v for v in views
        if (access is None or v.access_status == access)
        and (renewal is None or v.renewal_status == renewal)
        and (kind is None or v.payment_kind == kind)
    ]
    return views[offset:offset + limit], len(views)


def get_subscription(db: Session, clock: Clock, sub_id: uuid.UUID) -> SubscriptionOut:
    sub = db.get(Subscription, sub_id)
    if sub is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Abonnement introuvable")
    return _views(db, clock, [sub])[0]


def list_payments(
    db: Session, *, subscription_id: uuid.UUID | None, member_id: uuid.UUID | None,
    pay_status: str | None, limit: int, offset: int, clock: Clock | None = None,
    method: str | None = None, date_from: date | None = None, date_to: date | None = None,
    kind: str | None = None, changed: bool | None = None, sort: str = "-paid_at",
) -> tuple[list[PaymentOut], int, PaymentTotals]:
    """Filtres puis tri (date de paiement, puis identifiant), totaux sur TOUS les résultats filtrés,
    pagination en dernier. Dates : jours UTC inclusifs, comme le reste du backend."""
    conds = []
    if subscription_id is not None:
        conds.append(Payment.subscription_id == subscription_id)
    if member_id is not None:
        conds.append(Subscription.member_id == member_id)
    if pay_status is not None:
        conds.append(Payment.status == pay_status)
    if method is not None:
        conds.append(PaymentMethod.code == method)
    if date_from is not None:
        conds.append(Payment.paid_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to is not None:
        conds.append(Payment.paid_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    order = (Payment.paid_at.asc(), Payment.id.asc()) if sort == "paid_at" else (Payment.paid_at.desc(), Payment.id.asc())
    rows = db.execute(
        select(Payment, PaymentMethod.code)
        .join(PaymentMethod, PaymentMethod.id == Payment.payment_method_id)
        .join(Subscription, Subscription.id == Payment.subscription_id)
        .where(*conds)
        .order_by(*order)
    ).all()

    subs = db.execute(
        select(Subscription).where(Subscription.id.in_({p.subscription_id for p, _ in rows}))
    ).scalars().all() if rows else []
    views = {v.id: v for v in _views(db, clock or _SystemClock(), list(subs))}

    items: list[PaymentOut] = []
    for pay, code in rows:
        v = views[pay.subscription_id]
        if kind is not None and v.payment_kind != kind:
            continue
        if changed is not None and v.plan_changed != changed:
            continue
        items.append(_payment_out(pay, code).model_copy(update={
            "member_id": v.member_id, "member_name": v.member_name, "plan_code": v.plan_code,
            "plan_name": v.plan_name, "payment_kind": v.payment_kind, "plan_changed": v.plan_changed,
        }))

    confirmed = [p for p in items if p.status == "CONFIRMED"]
    totals = PaymentTotals(
        count=len(items), confirmed_count=len(confirmed), voided_count=len(items) - len(confirmed),
        collected_xof=sum(_xof(p) for p in confirmed),
    )
    return items[offset:offset + limit], len(items), totals


def _xof(p: PaymentOut) -> int:
    """Montant encaissé en XOF d'un paiement : valeur enregistrée, aucune conversion recalculée."""
    if p.currency == "XOF":
        return p.amount
    return p.settled_amount or 0


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
        raise _err(422, "La date de paiement ne peut pas être dans le futur")
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
        raise _err(422, "Moyen de paiement introuvable ou inactif")
    try:
        settled, settled_cur, rate = settlement_for(sub.currency, sub.price_amount, info.settled_amount)
    except ValueError as exc:
        raise _err(422, str(exc))
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
        raise _err(422, "Plan illimité : aucun paiement")
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
        raise _err(422, "Un renouvellement ne peut pas viser un plan illimité")

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
