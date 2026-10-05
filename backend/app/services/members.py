"""Membres et identités clientes. Chaque écriture et son audit sont dans la même transaction."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.clock import Clock
from app.models.clients import ClientIdentity, Member
from app.schemas.members import IdentityIn, MemberIn
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
    db: Session, *, q: str | None, include_archived: bool, limit: int, offset: int,
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
    members = db.execute(
        stmt.order_by(Member.created_at.desc(), Member.id).limit(limit).offset(offset)
    ).scalars().all()
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
