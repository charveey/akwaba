"""Verdict de chaque identité observée. Lecture seule : aucune écriture, aucun audit, aucune action réseau."""
from collections import Counter
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.domain.clock import Clock
from app.domain.identity import evaluate_identity, best_access
from app.domain.subscriptions import access_status
from app.models.billing import Subscription
from app.models.clients import ClientIdentity, Member
from app.models.infra import ExitNodeIdentity, ExitNodeObservation, ProtectedIdentity
from app.schemas.identities import IdentityEvaluationOut, IdentityEvaluationPage, MemberRef

RECENCY = timedelta(minutes=5)       # « actif maintenant » = observation confirmée dans ce délai
HORIZON = timedelta(days=7)          # recherche de la dernière activité confirmée


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def evaluate_identities(
    db: Session, clock: Clock, *, verdict: Optional[str], active_now: Optional[bool], q: Optional[str],
    limit: int, offset: int, recency: timedelta = RECENCY,
) -> IdentityEvaluationPage:
    now, today = clock.now(), clock.today()

    stmt = select(ExitNodeIdentity)
    if q:
        stmt = stmt.where(ExitNodeIdentity.login_name.ilike(f"%{_escape_like(q.strip().lower())}%", escape="\\"))
    identities = db.execute(
        stmt.order_by(ExitNodeIdentity.last_seen_at.desc(), ExitNodeIdentity.login_name)
    ).scalars().all()
    ids = [i.id for i in identities]
    logins = {i.login_name for i in identities}

    last_confirmed: Dict = {}
    if ids:
        last_confirmed = dict(db.execute(
            select(ExitNodeObservation.identity_id, func.max(ExitNodeObservation.observed_at))
            .where(ExitNodeObservation.identity_id.in_(ids),
                   ExitNodeObservation.active_confirmed.is_(True),
                   ExitNodeObservation.observed_at >= now - HORIZON)
            .group_by(ExitNodeObservation.identity_id)
        ).all())

    protected = set(db.execute(
        select(ProtectedIdentity.login_name).where(ProtectedIdentity.removed_at.is_(None))
    ).scalars())

    matches: Dict = {}
    if logins:
        rows = db.execute(
            select(ClientIdentity.login_name, ClientIdentity.id, Member.id, Member.full_name)
            .join(Member, Member.id == ClientIdentity.member_id)
            .where(ClientIdentity.provider == "tailscale", ClientIdentity.is_active.is_(True),
                   Member.archived_at.is_(None), ClientIdentity.login_name.in_(logins))
        ).all()
        matches = {r[0]: (r[1], r[2], r[3]) for r in rows}

    accesses: Dict = {}
    member_ids = {m[1] for m in matches.values()}
    if member_ids:
        for sub in db.execute(select(Subscription).where(Subscription.member_id.in_(member_ids))).scalars():
            accesses.setdefault(sub.member_id, []).append(access_status(
                today=today, starts_on=sub.starts_on, expires_on=sub.expires_on, is_unlimited=sub.is_unlimited,
                grace_period_days=sub.grace_period_days, payment_status=sub.payment_status,
                canceled=sub.canceled_at is not None, suspended=sub.suspended_at is not None,
            ))

    evaluated: List[IdentityEvaluationOut] = []
    for ident in identities:
        confirmed_at = last_confirmed.get(ident.id)
        active = confirmed_at is not None and confirmed_at >= now - recency
        match = matches.get(ident.login_name)
        member_accesses = accesses.get(match[1], []) if match else []
        result = evaluate_identity(
            resolved=True, active_confirmed=active, is_protected=ident.login_name in protected,
            member_linked=match is not None, accesses=member_accesses,
        )
        best = best_access(member_accesses)
        evaluated.append(IdentityEvaluationOut(
            id=ident.id, exit_node_id=ident.exit_node_id, login_name=ident.login_name, is_sharee=ident.is_sharee,
            first_seen_at=ident.first_seen_at, last_seen_at=ident.last_seen_at,
            last_confirmed_activity_at=confirmed_at, active_now=active,
            verdict=result.verdict.value, reason=result.reason, protected=ident.login_name in protected,
            member=MemberRef(id=match[1], full_name=match[2]) if match else None,
            client_identity_id=match[0] if match else None,
            access=best.value if best else None,
        ))

    counts = dict(Counter(e.verdict for e in evaluated))
    shown = [e for e in evaluated
             if (verdict is None or e.verdict == verdict) and (active_now is None or e.active_now == active_now)]

    unresolved = db.execute(
        select(func.count(distinct(ExitNodeObservation.tailscale_ip))).where(
            ExitNodeObservation.resolution_status == "UNRESOLVED",
            ExitNodeObservation.observed_at >= now - recency)
    ).scalar_one()

    return IdentityEvaluationPage(
        items=shown[offset:offset + limit], total=len(shown), counts=counts,
        unresolved_recent_sources=int(unresolved), evaluated_at=now,
        recency_seconds=int(recency.total_seconds()), limit=limit, offset=offset,
    )
