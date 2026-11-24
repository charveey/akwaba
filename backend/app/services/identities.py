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
from app.core.config import get_settings
from app.domain import quarantine as qd
from app.models.infra import (
    ExitNodeIdentity, ExitNodeObservation, ExitNodePeer, IdentityExemption, ProtectedIdentity, QuarantineRecord,
)
from app.schemas.identities import (
    ContactMatch, DeviceOut, IdentityEvaluationOut, IdentityEvaluationPage, MemberRef, ObservationOut,
    OpenRecordRef, UnresolvedPage, UnresolvedSource,
)

RECENCY = timedelta(minutes=5)       # « actif maintenant » = observation confirmée dans ce délai
HORIZON = timedelta(days=7)          # recherche de la dernière activité confirmée


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def evaluate_identities(
    db: Session, clock: Clock, *, verdict: Optional[str], active_now: Optional[bool], q: Optional[str],
    limit: int, offset: int, recency: timedelta = RECENCY,
    is_protected: Optional[bool] = None, has_member: Optional[bool] = None, ambiguous: Optional[bool] = None,
    open_record: Optional[bool] = None,
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

    evaluated = _enrich(db, identities, evaluated)
    counts = dict(Counter(e.verdict for e in evaluated))
    shown = [e for e in evaluated
             if (verdict is None or e.verdict == verdict) and (active_now is None or e.active_now == active_now)
             and (is_protected is None or e.protected == is_protected)
             and (has_member is None or (e.member is not None) == has_member)
             and (ambiguous is None or bool(e.ambiguity) == ambiguous)
             and (open_record is None or (e.open_record is not None) == open_record)]

    unresolved = db.execute(
        select(func.count(distinct(ExitNodeObservation.tailscale_ip))).where(
            ExitNodeObservation.resolution_status == "UNRESOLVED",
            ExitNodeObservation.observed_at >= now - recency)
    ).scalar_one()

    return IdentityEvaluationPage(
        items=shown[offset:offset + limit], total=len(shown), counts=counts,
        unresolved_recent_sources=int(unresolved), evaluated_at=now,
        recency_seconds=int(recency.total_seconds()), limit=limit, offset=offset,
        exit_node_reference_days=get_settings().retention_observations_days,
    )


# ---------------------------------------------------------------- Lot 11c : enrichissement (lecture seule)

ARCHIVED_MEMBER_LOGIN = "ARCHIVED_MEMBER_LOGIN"
CONTACT_EMAIL_MATCH = "CONTACT_EMAIL_MATCH"
MULTIPLE_EXIT_NODES = "MULTIPLE_EXIT_NODES"


def _enrich(db: Session, identities: list, evaluated: List[IdentityEvaluationOut]) -> List[IdentityEvaluationOut]:
    """Ajoute appareils, dossier ouvert, exemption, protections et signaux d'ambiguïté.

    Les signaux sont STRICTEMENT informatifs : ils ne modifient ni le verdict, ni une association,
    ni un dossier (aucune écriture dans ce module).
    """
    if not identities:
        return evaluated
    ids = [i.id for i in identities]
    logins = {i.login_name for i in identities}
    by_id = {i.id: i for i in identities}

    devices: Dict = {}
    for p in db.execute(select(ExitNodePeer).where(ExitNodePeer.identity_id.in_(ids))
                        .order_by(ExitNodePeer.last_seen_at.desc(), ExitNodePeer.stable_id)).scalars():
        devices.setdefault(p.identity_id, []).append(DeviceOut(
            stable_id=p.stable_id, hostname=p.hostname, os=p.os, tailscale_ip=p.tailscale_ip,
            last_seen_at=p.last_seen_at))
    open_records = {r.exit_node_identity_id: OpenRecordRef(id=r.id, status=r.status) for r in db.execute(
        select(QuarantineRecord).where(QuarantineRecord.exit_node_identity_id.in_(ids),
                                       QuarantineRecord.status.in_(qd.OPEN_STATUSES))).scalars()}
    exempt = set(db.execute(select(IdentityExemption.exit_node_identity_id).where(
        IdentityExemption.exit_node_identity_id.in_(ids), IdentityExemption.ended_at.is_(None))).scalars())
    sources: Dict = {}
    for lg, src in db.execute(select(ProtectedIdentity.login_name, ProtectedIdentity.source).where(
            ProtectedIdentity.login_name.in_(logins), ProtectedIdentity.removed_at.is_(None))).all():
        sources.setdefault(lg, set()).add(src)

    # MULTIPLE_EXIT_NODES : Exit Nodes DISTINCTS ayant produit au moins une observation de ce login,
    # sur toutes les observations conservées (période = rétention des observations). Le nombre
    # d'observations n'intervient pas.
    nodes_per_login = dict(db.execute(
        select(ExitNodeIdentity.login_name, func.count(distinct(ExitNodeObservation.exit_node_id)))
        .join(ExitNodeObservation, ExitNodeObservation.identity_id == ExitNodeIdentity.id)
        .where(ExitNodeIdentity.login_name.in_(logins))
        .group_by(ExitNodeIdentity.login_name)).all())
    archived_logins = set(db.execute(
        select(ClientIdentity.login_name).join(Member, Member.id == ClientIdentity.member_id)
        .where(ClientIdentity.provider == "tailscale", ClientIdentity.login_name.in_(logins),
               Member.archived_at.is_not(None))).scalars())
    contacts: Dict = {}
    for mid, name, email in db.execute(select(Member.id, Member.full_name, Member.email).where(
            Member.email.in_(logins), Member.archived_at.is_(None))).all():
        contacts.setdefault(email, []).append((mid, name))

    out = []
    for e in evaluated:
        ident = by_id[e.id]
        flags = []
        if e.login_name in archived_logins and e.member is None:
            flags.append(ARCHIVED_MEMBER_LOGIN)
        if any(e.member is None or e.member.id != mid for mid, _ in contacts.get(e.login_name, [])):
            flags.append(CONTACT_EMAIL_MATCH)
        node_count = int(nodes_per_login.get(e.login_name, 0)) or 1
        if node_count >= 2:
            flags.append(MULTIPLE_EXIT_NODES)
        out.append(e.model_copy(update={
            "display_name": ident.display_name, "tailscale_user_id": ident.tailscale_user_id,
            "devices": devices.get(e.id, []), "open_record": open_records.get(e.id),
            "exempt": e.id in exempt, "protection_sources": sorted(sources.get(e.login_name, ())),
            "ambiguity": flags, "exit_node_count": node_count,
        }))
    return out


def evaluate_one(db: Session, clock: Clock, identity_id) -> Optional[IdentityEvaluationOut]:
    ident = db.get(ExitNodeIdentity, identity_id)
    if ident is None:
        return None
    page = evaluate_identities(db, clock, verdict=None, active_now=None, q=ident.login_name, limit=10_000, offset=0)
    return next((e for e in page.items if e.id == identity_id), None)


def contact_matches(db: Session, login: str, linked_member_id) -> List[ContactMatch]:
    rows = db.execute(select(Member.id, Member.full_name).where(
        Member.email == login, Member.archived_at.is_(None)).order_by(Member.full_name, Member.id)).all()
    return [ContactMatch(id=mid, full_name=name) for mid, name in rows if mid != linked_member_id]


def recent_observations(db: Session, identity_id, limit: int = 50) -> List[ObservationOut]:
    rows = db.execute(
        select(ExitNodeObservation, ExitNodePeer.hostname)
        .outerjoin(ExitNodePeer, ExitNodePeer.id == ExitNodeObservation.peer_id)
        .where(ExitNodeObservation.identity_id == identity_id)
        .order_by(ExitNodeObservation.observed_at.desc(), ExitNodeObservation.id.desc()).limit(limit)).all()
    return [ObservationOut(
        observed_at=o.observed_at, window_seconds=o.window_seconds, active_flows=o.active_flows,
        replied_flows=o.replied_flows, new_flows=o.new_flows, active_confirmed=o.active_confirmed, hostname=host,
    ) for o, host in rows]


def unresolved_sources(db: Session, clock: Clock, hours: int) -> UnresolvedPage:
    """Observations UNRESOLVED regroupées par (Exit Node, IP Tailscale). Aucune identité n'est
    supposée ni rapprochée : ces sources ne produisent jamais de dossier ni de décision."""
    now = clock.now()
    rows = db.execute(
        select(ExitNodeObservation.exit_node_id, ExitNodeObservation.tailscale_ip, func.count(),
               func.min(ExitNodeObservation.observed_at), func.max(ExitNodeObservation.observed_at))
        .where(ExitNodeObservation.resolution_status == "UNRESOLVED",
               ExitNodeObservation.observed_at >= now - timedelta(hours=hours))
        .group_by(ExitNodeObservation.exit_node_id, ExitNodeObservation.tailscale_ip)
        .order_by(func.max(ExitNodeObservation.observed_at).desc(), ExitNodeObservation.tailscale_ip)).all()
    return UnresolvedPage(items=[UnresolvedSource(
        exit_node_id=n, tailscale_ip=ip, observations=int(c), first_observed_at=f, last_observed_at=l)
        for n, ip, c, f, l in rows], hours=hours, evaluated_at=now)
