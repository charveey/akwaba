import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_clock, require_admin, require_csrf
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.members import IdentityIn, IdentityOut, MemberIn, MemberOut, MemberPage, member_out
from app.services import members as svc
from app.services.auth import AuthContext

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=MemberPage, dependencies=[Depends(require_admin)])
def list_(
    q: str | None = Query(default=None, max_length=100),
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> MemberPage:
    rows, total = svc.list_members(db, q=q, include_archived=include_archived, limit=limit, offset=offset)
    return MemberPage(items=[member_out(m, i) for m, i in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def create(
    body: MemberIn, request: Request, ctx: AuthContext = Depends(require_csrf), db: Session = Depends(get_db),
) -> MemberOut:
    return member_out(*svc.create_member(db, ctx, body, client_ip(request)))


@router.get("/{member_id}", response_model=MemberOut, dependencies=[Depends(require_admin)])
def read(member_id: uuid.UUID, db: Session = Depends(get_db)) -> MemberOut:
    return member_out(*svc.get_member(db, member_id))


@router.put("/{member_id}", response_model=MemberOut)
def update(
    member_id: uuid.UUID, body: MemberIn, request: Request,
    ctx: AuthContext = Depends(require_csrf), db: Session = Depends(get_db),
) -> MemberOut:
    return member_out(*svc.update_member(db, ctx, member_id, body, client_ip(request)))


@router.post("/{member_id}/archive", response_model=MemberOut)
def archive(
    member_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> MemberOut:
    return member_out(*svc.archive_member(db, ctx, clock, member_id, client_ip(request)))


@router.post("/{member_id}/identities", response_model=IdentityOut, status_code=status.HTTP_201_CREATED)
def link(
    member_id: uuid.UUID, body: IdentityIn, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> IdentityOut:
    return IdentityOut.model_validate(svc.link_identity(db, ctx, clock, member_id, body, client_ip(request)))


@router.post("/{member_id}/identities/{identity_id}/unlink", response_model=IdentityOut)
def unlink(
    member_id: uuid.UUID, identity_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> IdentityOut:
    return IdentityOut.model_validate(
        svc.unlink_identity(db, ctx, clock, member_id, identity_id, client_ip(request))
    )
