import uuid
from typing import List

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_clock, require_admin, require_csrf
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.protected import ProtectedIn, ProtectedOut
from app.services import protected as svc
from app.services.auth import AuthContext

router = APIRouter(prefix="/protected-identities", tags=["protected-identities"])


@router.get("", response_model=List[ProtectedOut], dependencies=[Depends(require_admin)])
def list_(include_removed: bool = False, db: Session = Depends(get_db)) -> List[ProtectedOut]:
    return svc.list_protected(db, include_removed=include_removed)


@router.post("", response_model=ProtectedOut, status_code=status.HTTP_201_CREATED)
def add(body: ProtectedIn, request: Request, ctx: AuthContext = Depends(require_csrf),
        db: Session = Depends(get_db)) -> ProtectedOut:
    return svc.add_protected(db, login=body.login_name, reason=body.reason, admin_id=ctx.admin.id,
                             ip=client_ip(request))


@router.post("/{protected_id}/remove", response_model=ProtectedOut)
def remove(protected_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_csrf),
           db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> ProtectedOut:
    return svc.remove_protected(db, protected_id=protected_id, now=clock.now(), admin_id=ctx.admin.id,
                                ip=client_ip(request))
