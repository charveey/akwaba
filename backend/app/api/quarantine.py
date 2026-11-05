import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_clock, require_admin, require_csrf
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.quarantine import (
    DecisionPage, ExemptionOut, ExemptionPage, QuarantineDetail, QuarantineOut, QuarantinePage, ResolveIn,
)
from app.services import quarantine as svc
from app.services import quarantine_views as views
from app.services.auth import AuthContext

quarantine_router = APIRouter(prefix="/quarantine", tags=["quarantine"])
decisions_router = APIRouter(prefix="/enforcement-decisions", tags=["quarantine"],
                             dependencies=[Depends(require_admin)])
exemptions_router = APIRouter(prefix="/identity-exemptions", tags=["quarantine"])

StatusName = Literal["PENDING", "AUTHORIZED", "EXPIRED", "PENDING_REVOCATION", "MANUAL_REVIEW", "DISMISSED"]
DecisionName = Literal["WOULD_BLOCK", "WOULD_UNBLOCK"]


@quarantine_router.get("", response_model=QuarantinePage, dependencies=[Depends(require_admin)])
def list_records(
    status: Optional[StatusName] = None,
    open_only: bool = False,
    q: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> QuarantinePage:
    return views.list_records(db, status=status, q=q, open_only=open_only, limit=limit, offset=offset)


@quarantine_router.get("/{record_id}", response_model=QuarantineDetail, dependencies=[Depends(require_admin)])
def read_record(record_id: uuid.UUID, db: Session = Depends(get_db)) -> QuarantineDetail:
    return views.get_record(db, record_id)


def _resolve(action: str, record_id: uuid.UUID, body: Optional[ResolveIn], request: Request,
             ctx: AuthContext, db: Session, clock: Clock) -> QuarantineOut:
    svc.resolve_record(db, record_id=record_id, action=action, admin_id=ctx.admin.id, now=clock.now(),
                       note=body.note if body else None, ip=client_ip(request))
    return views.get_record(db, record_id).record


@quarantine_router.post("/{record_id}/authorize", response_model=QuarantineOut)
def authorize(record_id: uuid.UUID, request: Request, body: Optional[ResolveIn] = None,
              ctx: AuthContext = Depends(require_csrf), db: Session = Depends(get_db),
              clock: Clock = Depends(get_clock)) -> QuarantineOut:
    return _resolve("AUTHORIZE", record_id, body, request, ctx, db, clock)


@quarantine_router.post("/{record_id}/dismiss", response_model=QuarantineOut)
def dismiss(record_id: uuid.UUID, request: Request, body: Optional[ResolveIn] = None,
            ctx: AuthContext = Depends(require_csrf), db: Session = Depends(get_db),
            clock: Clock = Depends(get_clock)) -> QuarantineOut:
    return _resolve("DISMISS", record_id, body, request, ctx, db, clock)


@decisions_router.get("", response_model=DecisionPage)
def list_decisions(
    decision: Optional[DecisionName] = None,
    q: Optional[str] = Query(default=None, max_length=100),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> DecisionPage:
    return views.list_decisions(db, decision=decision, q=q, date_from=date_from, date_to=date_to,
                                limit=limit, offset=offset)


@exemptions_router.get("", response_model=ExemptionPage, dependencies=[Depends(require_admin)])
def list_exemptions(
    active: Optional[bool] = None,
    q: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ExemptionPage:
    return views.list_exemptions(db, active=active, q=q, limit=limit, offset=offset)


@exemptions_router.post("/{exemption_id}/end", response_model=ExemptionOut)
def end_exemption(exemption_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_csrf),
                  db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> ExemptionOut:
    svc.end_exemption(db, exemption_id=exemption_id, admin_id=ctx.admin.id, now=clock.now(), ip=client_ip(request))
    return views.get_exemption(db, exemption_id)
