"""Rapports en XOF. Lecture seule : aucune écriture, aucun audit."""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import DateTime, cast, func, literal_column, select
from sqlalchemy.orm import Session

from app.domain.reports import Unit, merge_profit, period_label
from app.models.admin import AdminUser
from app.models.audit import AuditLog
from app.models.billing import Payment, PaymentMethod, Subscription, SubscriptionPlan
from app.models.finance import Expense, ExpenseCategory
from app.schemas.reports import AmountReport, AmountRow, AuditEntry, ProfitReport, ProfitRow

_UNITS = ("month", "quarter", "year")
# Revenu encaissé : montant réglé en XOF, ou montant du paiement s'il est déjà en XOF.
_REVENUE = func.coalesce(Payment.settled_amount, Payment.amount)


def check_range(date_from: date | None, date_to: date | None) -> None:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from doit précéder date_to")


def _start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _payment_conds(date_from: date | None, date_to: date | None) -> list:
    conds = [Payment.status == "CONFIRMED"]
    if date_from:
        conds.append(Payment.paid_at >= _start(date_from))
    if date_to:
        conds.append(Payment.paid_at < _start(date_to + timedelta(days=1)))
    return conds


def _expense_conds(date_from: date | None, date_to: date | None) -> list:
    conds = [Expense.status == "ACTIVE"]
    if date_from:
        conds.append(Expense.expense_date >= date_from)
    if date_to:
        conds.append(Expense.expense_date <= date_to)
    return conds


def _bucket(unit: str, ts):
    # Unité en littéral SQL (liste blanche) : un paramètre lié ferait échouer le GROUP BY.
    assert unit in _UNITS
    return func.date_trunc(literal_column(f"'{unit}'"), ts)


def _revenue_series(db: Session, unit: str, date_from, date_to) -> dict[date, tuple[int, int]]:
    key = _bucket(unit, func.timezone(literal_column("'UTC'"), Payment.paid_at))
    rows = db.execute(
        select(key, func.count(), func.sum(_REVENUE)).select_from(Payment)
        .where(*_payment_conds(date_from, date_to)).group_by(key).order_by(key)
    ).all()
    return {k.date(): (int(c), int(s)) for k, c, s in rows}


def _expense_series(db: Session, unit: str, date_from, date_to) -> dict[date, tuple[int, int]]:
    key = _bucket(unit, cast(Expense.expense_date, DateTime))
    rows = db.execute(
        select(key, func.count(), func.sum(Expense.amount)).select_from(Expense)
        .where(*_expense_conds(date_from, date_to)).group_by(key).order_by(key)
    ).all()
    return {k.date(): (int(c), int(s)) for k, c, s in rows}


def _time_rows(unit: Unit, series: dict[date, tuple[int, int]]) -> list[AmountRow]:
    return [
        AmountRow(key=period_label(unit, s), label=period_label(unit, s), period_start=s, count=c, amount_xof=a)
        for s, (c, a) in sorted(series.items())
    ]


def _report(date_from, date_to, group_by: str, rows: list[AmountRow]) -> AmountReport:
    return AmountReport(
        date_from=date_from, date_to=date_to, group_by=group_by,
        count=sum(r.count for r in rows), total_xof=sum(r.amount_xof for r in rows), rows=rows,
    )


def revenue_report(db: Session, *, group_by: str, date_from, date_to) -> AmountReport:
    check_range(date_from, date_to)
    if group_by in _UNITS:
        return _report(date_from, date_to, group_by, _time_rows(group_by, _revenue_series(db, group_by, date_from, date_to)))
    rows = db.execute(
        select(PaymentMethod.code, PaymentMethod.name, func.count(), func.sum(_REVENUE))
        .select_from(Payment).join(PaymentMethod, PaymentMethod.id == Payment.payment_method_id)
        .where(*_payment_conds(date_from, date_to))
        .group_by(PaymentMethod.code, PaymentMethod.name).order_by(PaymentMethod.name)
    ).all()
    return _report(date_from, date_to, "payment_method", [
        AmountRow(key=code, label=name, period_start=None, count=int(c), amount_xof=int(s))
        for code, name, c, s in rows
    ])


def revenue_by_plan_report(db: Session, *, date_from, date_to) -> AmountReport:
    check_range(date_from, date_to)
    rows = db.execute(
        select(SubscriptionPlan.code, SubscriptionPlan.name, func.count(), func.sum(_REVENUE))
        .select_from(Payment)
        .join(Subscription, Subscription.id == Payment.subscription_id)
        .join(SubscriptionPlan, SubscriptionPlan.id == Subscription.plan_id)
        .where(*_payment_conds(date_from, date_to))
        .group_by(SubscriptionPlan.code, SubscriptionPlan.name, SubscriptionPlan.sort_order)
        .order_by(SubscriptionPlan.sort_order)
    ).all()
    return _report(date_from, date_to, "plan", [
        AmountRow(key=code, label=name, period_start=None, count=int(c), amount_xof=int(s))
        for code, name, c, s in rows
    ])


def expenses_report(db: Session, *, group_by: str, date_from, date_to) -> AmountReport:
    check_range(date_from, date_to)
    if group_by in _UNITS:
        return _report(date_from, date_to, group_by, _time_rows(group_by, _expense_series(db, group_by, date_from, date_to)))
    rows = db.execute(
        select(ExpenseCategory.id, ExpenseCategory.name, func.count(), func.sum(Expense.amount))
        .select_from(Expense).join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .where(*_expense_conds(date_from, date_to))
        .group_by(ExpenseCategory.id, ExpenseCategory.name, ExpenseCategory.sort_order)
        .order_by(ExpenseCategory.sort_order)
    ).all()
    return _report(date_from, date_to, "category", [
        AmountRow(key=str(cid), label=name, period_start=None, count=int(c), amount_xof=int(s))
        for cid, name, c, s in rows
    ])


def profit_report(db: Session, *, group_by: Unit, date_from, date_to) -> ProfitReport:
    check_range(date_from, date_to)
    revenue = {s: a for s, (_, a) in _revenue_series(db, group_by, date_from, date_to).items()}
    expenses = {s: a for s, (_, a) in _expense_series(db, group_by, date_from, date_to).items()}
    rows = [
        ProfitRow(key=period_label(group_by, s), label=period_label(group_by, s), period_start=s,
                  revenue_xof=r, expenses_xof=e, profit_xof=p)
        for s, r, e, p in merge_profit(revenue, expenses)
    ]
    rev, exp = sum(r.revenue_xof for r in rows), sum(r.expenses_xof for r in rows)
    return ProfitReport(date_from=date_from, date_to=date_to, group_by=group_by,
                        revenue_xof=rev, expenses_xof=exp, profit_xof=rev - exp, rows=rows)


def list_audit(
    db: Session, *, action: str | None, entity_type: str | None, entity_id: str | None,
    actor_admin_id: uuid.UUID | None, date_from: date | None, date_to: date | None, limit: int, offset: int,
) -> tuple[list[AuditEntry], int]:
    check_range(date_from, date_to)
    conds = []
    if action:
        conds.append(AuditLog.action == action)
    if entity_type:
        conds.append(AuditLog.entity_type == entity_type)
    if entity_id:
        conds.append(AuditLog.entity_id == entity_id)
    if actor_admin_id:
        conds.append(AuditLog.actor_admin_id == actor_admin_id)
    if date_from:
        conds.append(AuditLog.occurred_at >= _start(date_from))
    if date_to:
        conds.append(AuditLog.occurred_at < _start(date_to + timedelta(days=1)))
    total = db.execute(select(func.count()).select_from(AuditLog).where(*conds)).scalar_one()
    rows = db.execute(
        select(AuditLog, AdminUser.email)
        .outerjoin(AdminUser, AdminUser.id == AuditLog.actor_admin_id)
        .where(*conds).order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    ).all()
    return [
        AuditEntry(
            id=a.id, occurred_at=a.occurred_at, actor_admin_id=a.actor_admin_id, actor_email=email,
            actor_label=a.actor_label, action=a.action, entity_type=a.entity_type, entity_id=a.entity_id,
            details=a.details, ip_address=a.ip_address,
        )
        for a, email in rows
    ], total
