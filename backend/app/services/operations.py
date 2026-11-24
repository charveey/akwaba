"""Lot 11d : rapport opérationnel et vue d'ensemble. Lecture seule ; calculs en UTC ; aucune règle
métier réécrite (résumé membre du lot 11b, rapports existants, compteurs de quarantaine du lot 11c,
santé de l'agent = fonction du réconciliateur)."""
from collections import Counter
from datetime import date, datetime, timezone
from typing import Dict, List, Tuple

from fastapi import HTTPException
from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from app.domain import quarantine as qd
from app.domain.clock import Clock
from app.domain.reports import period_label
from app.models.billing import Subscription
from app.models.clients import Member
from app.models.infra import AgentHeartbeat, ExitNode, ExitNodeIdentity, QuarantineRecord
from app.schemas.dashboard import (
    AgentIndicator, DashboardOut, Indicators, MonthCount, OperationsReport, OperationsRow, PlanCount, StatusCount,
)
from app.schemas.reports import AmountRow
from app.services import billing, members, quarantine, reports
from app.services import identities as identities_svc
from app.services import quarantine_views


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    idx = d.year * 12 + d.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


def months_between(first: date, last: date) -> List[date]:
    out, cur = [], month_start(first)
    while cur <= last:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def _by_month(db: Session, column, first: date, last: date) -> Dict[date, int]:
    key = func.date_trunc(literal_column("'month'"), func.timezone(literal_column("'UTC'"), column))
    rows = db.execute(
        select(key, func.count()).where(
            column >= datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc),
            column < datetime.combine(add_months(last, 1), datetime.min.time(), tzinfo=timezone.utc),
        ).group_by(key)).all()
    return {k.date(): int(c) for k, c in rows}


def _summaries(db: Session, clock: Clock):
    ids = list(db.execute(select(Member.id).where(Member.archived_at.is_(None))).scalars())
    return ids, members.current_summaries(db, clock, ids)


def _snapshot(ids, summaries) -> Dict[str, int]:
    counts = Counter(summaries[i].access_status if i in summaries else "NONE" for i in ids)
    return {k: counts.get(k, 0) for k in ("ACTIVE", "GRACE_PERIOD", "EXPIRED", "SUSPENDED", "NONE")}


def _kind_months(db: Session, clock: Clock, first: date, last: date) -> Dict[date, Counter]:
    """Statuts de paiement (calculés par billing) comptés au mois UTC du premier paiement confirmé
    (ou de la création pour un abonnement offert)."""
    subs = list(db.execute(select(Subscription)).scalars())
    out: Dict[date, Counter] = {}
    for v in billing.subscription_views(db, clock, subs):
        if v.payment_kind in (None, "PENDING"):
            continue
        confirmed = [p.paid_at for p in v.payments if p.status == "CONFIRMED"]
        at = min(confirmed) if confirmed else v.created_at
        m = month_start(at.astimezone(timezone.utc).date())
        if not (month_start(first) <= m <= month_start(last)):
            continue
        c = out.setdefault(m, Counter())
        c[v.payment_kind] += 1
        if v.plan_changed:
            c["PLAN_CHANGED"] += 1
    return out


def _range(clock: Clock, date_from, date_to) -> Tuple[date, date]:
    today = clock.today()
    last = date_to or today
    first = date_from or add_months(month_start(last), -11)
    if first > last:
        raise HTTPException(422, "date_from doit précéder date_to")
    return first, last


def operations_report(db: Session, clock: Clock, *, date_from=None, date_to=None) -> OperationsReport:
    first, last = _range(clock, date_from, date_to)
    ids, summaries = _summaries(db, clock)
    new_members = _by_month(db, Member.created_at, first, last)
    opened = _by_month(db, QuarantineRecord.started_at, first, last)
    seen = _by_month(db, ExitNodeIdentity.first_seen_at, first, last)
    kinds = _kind_months(db, clock, first, last)
    rows = []
    for m in months_between(first, last):
        k = kinds.get(m, Counter())
        rows.append(OperationsRow(
            month=m, label=period_label("month", m), new_members=new_members.get(m, 0),
            new_member_subscriptions=k["NEW_MEMBER"], renewals=k["RENEWED"], late_renewals=k["LATE_RENEWAL"],
            plan_changes=k["PLAN_CHANGED"], quarantine_opened=opened.get(m, 0), identities_first_seen=seen.get(m, 0),
        ))
    fields = ("new_members", "new_member_subscriptions", "renewals", "late_renewals", "plan_changes",
              "quarantine_opened", "identities_first_seen")
    return OperationsReport(
        evaluated_at=clock.now(), today=clock.today(), date_from=first, date_to=last,
        snapshot=_snapshot(ids, summaries), members_total=len(ids), rows=rows,
        totals={f: sum(getattr(r, f) for r in rows) for f in fields},
    )


def _fill_months(rows: List[AmountRow], months: List[date]) -> List[AmountRow]:
    by = {r.period_start: r for r in rows}
    return [by.get(m) or AmountRow(key=period_label("month", m), label=period_label("month", m), period_start=m,
                                   count=0, amount_xof=0) for m in months]


def dashboard(db: Session, clock: Clock) -> DashboardOut:
    now, today = clock.now(), clock.today()
    first_month = month_start(today)
    range_start = add_months(first_month, -11)
    months = months_between(range_start, today)

    ids, summaries = _summaries(db, clock)
    snap = _snapshot(ids, summaries)
    pending = db.execute(
        select(func.count()).select_from(Subscription).join(Member, Member.id == Subscription.member_id)
        .where(Subscription.payment_status == "PENDING", Subscription.canceled_at.is_(None),
               Member.archived_at.is_(None))).scalar_one()
    month_revenue = reports.revenue_report(db, group_by="month", date_from=first_month, date_to=today)
    q_counts = quarantine_views.list_records(db, status=None, q=None, open_only=False, limit=1, offset=0,
                                             clock=clock).counts
    unresolved = identities_svc.unresolved_sources(db, clock, 24)

    # Santé : la fonction EXACTE du réconciliateur, avec son seuil.
    health = quarantine._health(db, now, quarantine.HEALTH_MAX_AGE)
    nodes = db.execute(select(func.count()).select_from(ExitNode).where(ExitNode.is_active.is_(True))).scalar_one()
    last_hb = db.execute(select(func.max(AgentHeartbeat.received_at))).scalar_one()

    plan_counts: Dict[Tuple[str, str], int] = Counter(
        (s.plan_code, s.plan_name) for s in summaries.values() if s.access_status in ("ACTIVE", "GRACE_PERIOD"))
    plan_order = {code: i for i, code in enumerate(p.code for p in billing.list_plans(db))}

    base = db.execute(select(func.count()).select_from(Member).where(
        Member.created_at < datetime.combine(range_start, datetime.min.time(), tzinfo=timezone.utc))).scalar_one()
    new_by_month = _by_month(db, Member.created_at, range_start, today)
    cumulative, members_rows = int(base), []
    for m in months:
        cumulative += new_by_month.get(m, 0)
        members_rows.append(MonthCount(month=m, label=period_label("month", m),
                                       new_members=new_by_month.get(m, 0), cumulative=cumulative))

    return DashboardOut(
        evaluated_at=now, today=today, month_start=first_month, range_start=range_start,
        indicators=Indicators(
            members_total=len(ids), members_active=snap["ACTIVE"], members_grace=snap["GRACE_PERIOD"],
            subscriptions_expired=snap["EXPIRED"], payments_pending=int(pending),
            revenue_month_xof=month_revenue.total_xof, revenue_month_count=month_revenue.count,
            quarantine_open=sum(q_counts.get(s, 0) for s in qd.OPEN_STATUSES),
            unresolved_sources_24h=len(unresolved.items),
            agent=AgentIndicator(exit_nodes=int(nodes), healthy_nodes=sum(1 for v in health.values() if v),
                                 last_heartbeat_at=last_hb),
        ),
        revenue_by_month=_fill_months(
            reports.revenue_report(db, group_by="month", date_from=range_start, date_to=today).rows, months),
        subscriptions_by_plan=[PlanCount(plan_code=c, plan_name=n, count=k) for (c, n), k in
                               sorted(plan_counts.items(), key=lambda x: plan_order.get(x[0][0], 99))],
        members_by_month=members_rows,
        payments_by_method=reports.revenue_report(db, group_by="payment_method", date_from=range_start,
                                                  date_to=today).rows,
        quarantine_by_status=[StatusCount(status=s, count=int(q_counts.get(s, 0))) for s in
                              ("PENDING", "AUTHORIZED", "EXPIRED", "PENDING_REVOCATION", "MANUAL_REVIEW", "DISMISSED")],
    )
