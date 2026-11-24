"""Membres et identités clientes. Chaque écriture et son audit sont dans la même transaction."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.clock import Clock
from app.models.billing import Subscription
from app.models.clients import ClientIdentity, Member
from app.schemas.members import IdentityIn, MemberIn, MemberSubscriptionSummary
from app.services.audit import record
from app.services.auth import AuthContext

FIELDS = ("full_name", "email", "phone_e164", "country_code", "notes")


def _get(db: Session, member_id: uuid.UUID, *, lock: bool = False) -> Member:
    stmt = select(Member).where(Member.id == member_id)
    if lock:
        stmt = stmt.with_for_update()
    member = db.execute(stmt).scalar_one_or_none()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membre introuvable")
    return member


def _identities(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ClientIdentity]]:
    out: dict[uuid.UUID, list[ClientIdentity]] = {i: [] for i in ids}
    if ids:
        rows = db.execute(
            select(ClientIdentity).where(ClientIdentity.member_id.in_(ids))
            .order_by(ClientIdentity.linked_at, ClientIdentity.id)
        ).scalars()
        for row in rows:
            out[row.member_id].append(row)
    return out


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def get_member(db: Session, member_id: uuid.UUID) -> tuple[Member, list[ClientIdentity]]:
    member = _get(db, member_id)
    return member, _identities(db, [member.id])[member.id]


def list_members(
    db: Session, *, q: str | None, include_archived: bool, limit: int | None, offset: int,
) -> tuple[list[tuple[Member, list[ClientIdentity]]], int]:
    stmt = select(Member)
    if not include_archived:
        stmt = stmt.where(Member.archived_at.is_(None))
    if q and q.strip():
        p = _like(q.strip())
        login_match = select(ClientIdentity.id).where(
            ClientIdentity.member_id == Member.id,
            ClientIdentity.is_active.is_(True),
            ClientIdentity.login_name.ilike(p, escape="\\"),
        ).exists()
        stmt = stmt.where(or_(
            Member.full_name.ilike(p, escape="\\"),
            Member.email.ilike(p, escape="\\"),
            Member.phone_e164.ilike(p, escape="\\"),
            login_match,
        ))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    ordered = stmt.order_by(Member.created_at.desc(), Member.id)
    if limit is not None:
        ordered = ordered.limit(limit).offset(offset)
    members = db.execute(ordered).scalars().all()
    idents = _identities(db, [m.id for m in members])
    return [(m, idents[m.id]) for m in members], total


def create_member(db: Session, ctx: AuthContext, data: MemberIn, ip: str | None) -> tuple[Member, list[ClientIdentity]]:
    member = Member(**data.model_dump())
    db.add(member)
    db.flush()
    record(db, "member.created", actor_admin_id=ctx.admin.id, entity_type="member", entity_id=member.id, ip=ip)
    db.commit()
    return member, []


def update_member(
    db: Session, ctx: AuthContext, member_id: uuid.UUID, data: MemberIn, ip: str | None,
) -> tuple[Member, list[ClientIdentity]]:
    member = _get(db, member_id, lock=True)
    if member.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Membre archivé : modification impossible")
    new = data.model_dump()
    changed = [f for f in FIELDS if getattr(member, f) != new[f]]
    for f in changed:
        setattr(member, f, new[f])
    if changed:
        # Noms des champs seulement : jamais de donnée personnelle dans un journal append-only.
        record(db, "member.updated", actor_admin_id=ctx.admin.id, entity_type="member",
               entity_id=member.id, details={"changed_fields": changed}, ip=ip)
    db.commit()
    return member, _identities(db, [member.id])[member.id]


def archive_member(
    db: Session, ctx: AuthContext, clock: Clock, member_id: uuid.UUID, ip: str | None,
) -> tuple[Member, list[ClientIdentity]]:
    member = _get(db, member_id, lock=True)
    if member.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Membre déjà archivé")
    now = clock.now()
    member.archived_at = now
    unlinked = db.execute(
        update(ClientIdentity)
        .where(ClientIdentity.member_id == member.id, ClientIdentity.is_active.is_(True))
        .values(is_active=False, unlinked_at=now)
        .returning(ClientIdentity.login_name)
    ).scalars().all()
    record(db, "member.archived", actor_admin_id=ctx.admin.id, entity_type="member",
           entity_id=member.id, details={"unlinked_logins": sorted(unlinked)}, ip=ip)
    db.commit()
    return member, _identities(db, [member.id])[member.id]


def link_identity(
    db: Session, ctx: AuthContext, clock: Clock, member_id: uuid.UUID, data: IdentityIn, ip: str | None,
) -> ClientIdentity:
    member = _get(db, member_id, lock=True)
    if member.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Membre archivé : liaison impossible")
    existing = db.execute(
        select(ClientIdentity).where(
            ClientIdentity.provider == "tailscale",
            ClientIdentity.login_name == data.login_name,
            ClientIdentity.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if existing is not None:
        who = "ce membre" if existing.member_id == member.id else "un autre membre"
        raise HTTPException(status.HTTP_409_CONFLICT, f"Ce login est déjà actif sur {who}")
    ident = ClientIdentity(member_id=member.id, login_name=data.login_name, linked_at=clock.now())
    db.add(ident)
    try:
        db.flush()  # l'index unique partiel tranche une éventuelle course
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce login est déjà actif sur un membre")
    record(db, "identity.linked", actor_admin_id=ctx.admin.id, entity_type="client_identity",
           entity_id=ident.id, details={"member_id": str(member.id), "login_name": ident.login_name}, ip=ip)
    db.commit()
    return ident


def unlink_identity(
    db: Session, ctx: AuthContext, clock: Clock, member_id: uuid.UUID, identity_id: uuid.UUID, ip: str | None,
) -> ClientIdentity:
    _get(db, member_id, lock=True)
    ident = db.execute(
        select(ClientIdentity).where(ClientIdentity.id == identity_id).with_for_update()
    ).scalar_one_or_none()
    if ident is None or ident.member_id != member_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identité introuvable pour ce membre")
    if not ident.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Identité déjà déliée")
    ident.is_active = False
    ident.unlinked_at = clock.now()
    record(db, "identity.unlinked", actor_admin_id=ctx.admin.id, entity_type="client_identity",
           entity_id=ident.id, details={"member_id": str(member_id), "login_name": ident.login_name}, ip=ip)
    db.commit()
    return ident


def reassign_identity(
    db: Session, ctx: AuthContext, clock: Clock, member_id: uuid.UUID, identity_id: uuid.UUID,
    target_member_id: uuid.UUID, ip: str | None,
) -> ClientIdentity:
    """Lot 11c : réaffecte un login Tailscale d'un membre à un autre, en UNE transaction.

    Détache l'ancienne liaison et crée la nouvelle (l'historique des liaisons est conservé),
    avec un unique événement d'audit « identity.reassigned ». Toute erreur annule l'ensemble.
    """
    if target_member_id == member_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Le membre cible est déjà celui de cette identité")
    # Verrous dans un ordre stable (identifiants croissants) : pas d'interblocage entre deux réaffectations.
    locked = {}
    for mid in sorted((member_id, target_member_id), key=str):
        locked[mid] = db.execute(select(Member).where(Member.id == mid).with_for_update()).scalar_one_or_none()
    source, target = locked[member_id], locked[target_member_id]
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membre source introuvable")
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membre cible introuvable")
    if target.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Membre cible archivé : réaffectation impossible")
    old = db.execute(
        select(ClientIdentity).where(ClientIdentity.id == identity_id).with_for_update()
    ).scalar_one_or_none()
    if old is None or old.member_id != member_id:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identité introuvable pour ce membre")
    if not old.is_active:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Identité déjà déliée : utiliser un rattachement simple")
    now = clock.now()
    try:
        old.is_active = False
        old.unlinked_at = now
        db.flush()
        new = ClientIdentity(member_id=target.id, provider=old.provider, login_name=old.login_name, linked_at=now)
        db.add(new)
        db.flush()  # l'index unique partiel (un login actif = un membre) tranche toute incohérence
        record(db, "identity.reassigned", actor_admin_id=ctx.admin.id, entity_type="client_identity",
               entity_id=new.id, ip=ip, details={
                   "login_name": old.login_name,
                   "from_member_id": str(source.id), "to_member_id": str(target.id),
                   "from_client_identity_id": str(old.id), "to_client_identity_id": str(new.id),
               })
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Réaffectation impossible : association incohérente")
    except Exception:
        db.rollback()
        raise
    return new


# ---------------------------------------------------------------- Phase 11b : vue d'ensemble (lecture)

MEMBER_SORTS = ("-created", "created", "name", "-name", "expiry", "-expiry")


def current_summaries(
    db: Session, clock: Clock, member_ids: list[uuid.UUID],
) -> dict[uuid.UUID, MemberSubscriptionSummary]:
    """Abonnement actuel de chaque membre : dernier abonnement sans successeur (création la plus récente).

    Toutes les valeurs (accès, échéances, statut métier) viennent des fonctions de domaine du backend.
    """
    from app.services.billing import subscription_views  # import local : évite un cycle

    if not member_ids:
        return {}
    subs = db.execute(
        select(Subscription).where(Subscription.member_id.in_(member_ids))
        .order_by(Subscription.created_at, Subscription.id)
    ).scalars().all()
    succeeded = {s.previous_subscription_id for s in subs if s.previous_subscription_id is not None}
    current: dict[uuid.UUID, Subscription] = {}
    for s in subs:                      # ordre croissant : le dernier gagne
        if s.id not in succeeded:
            current[s.member_id] = s
    # Abonnement en vigueur : en remontant la chaîne depuis le dernier, le premier déjà commencé.
    by_id = {s.id: s for s in subs}
    today = clock.today()
    in_force: dict[uuid.UUID, Subscription] = {}
    for member_id, head in current.items():
        node = head
        while node is not None and node.starts_on > today:
            node = by_id.get(node.previous_subscription_id) if node.previous_subscription_id else None
        in_force[member_id] = node or head
    wanted = {s.id: s for s in [*current.values(), *in_force.values()]}
    views = {v.id: v for v in subscription_views(db, clock, list(wanted.values()))}
    out = {}
    for member_id, s in current.items():
        v = views[s.id]
        effective = views[in_force[member_id].id]
        out[member_id] = MemberSubscriptionSummary(
            subscription_id=v.id, plan_code=v.plan_code, plan_name=v.plan_name, starts_on=v.starts_on,
            expires_on=v.expires_on, display_end=v.display_end, grace_end=v.grace_end,
            is_unlimited=v.is_unlimited, access_status=effective.access_status, renewal_status=v.renewal_status,
            payment_status=v.payment_status, payment_kind=v.payment_kind, plan_changed=v.plan_changed,
            upcoming_starts_on=v.starts_on if v.starts_in_future and effective.id != v.id else None,
        )
    return out


def list_members_overview(
    db: Session, clock: Clock, *, q: str | None, include_archived: bool, limit: int, offset: int,
    access: str | None = None, plan_code: str | None = None, sort: str = "-created",
) -> tuple[list[tuple[Member, list[ClientIdentity], MemberSubscriptionSummary | None]], int]:
    """Filtres et tris calculés appliqués à TOUS les membres correspondants, puis pagination.

    access="NONE" inclut les membres sans aucun abonnement. Tri secondaire déterministe : identifiant.
    """
    rows, _ = list_members(db, q=q, include_archived=include_archived, limit=None, offset=0)
    summaries = current_summaries(db, clock, [m.id for m, _ in rows])
    items = [(m, idents, summaries.get(m.id)) for m, idents in rows]
    if access is not None:
        items = [it for it in items if (it[2].access_status if it[2] else "NONE") == access]
    if plan_code is not None:
        items = [it for it in items if it[2] is not None and it[2].plan_code == plan_code]

    items.sort(key=lambda it: str(it[0].id))                         # clé secondaire stable
    if sort in ("name", "-name"):
        items.sort(key=lambda it: it[0].full_name.casefold(), reverse=sort == "-name")
    elif sort in ("created", "-created"):
        items.sort(key=lambda it: it[0].created_at, reverse=sort == "-created")
    else:  # échéance : les membres sans échéance (aucun abonnement, illimité) toujours à la fin
        dated = [it for it in items if it[2] is not None and it[2].expires_on is not None]
        undated = [it for it in items if not (it[2] is not None and it[2].expires_on is not None)]
        dated.sort(key=lambda it: it[2].expires_on, reverse=sort == "-expiry")
        items = dated + undated
    return items[offset:offset + limit], len(items)
