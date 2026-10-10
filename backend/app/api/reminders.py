import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_clock, require_admin, require_csrf
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.reminders import GenerateResult, ReminderLink, ReminderOut, ReminderPage
from app.services import reminders as svc
from app.services.auth import AuthContext

router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.get("", response_model=ReminderPage, dependencies=[Depends(require_admin)])
def list_(
    status_: Literal["PENDING", "OPENED", "SENT", "CANCELED"] | None = Query(default=None, alias="status"),
    due_on: date | None = None,
    member_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ReminderPage:
    items, total = svc.list_reminders(
        db, rem_status=status_, due_on=due_on, member_id=member_id, limit=limit, offset=offset)
    return ReminderPage(items=items, total=total, limit=limit, offset=offset)


@router.post("/generate", response_model=GenerateResult)
def generate(
    request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> GenerateResult:
    return svc.generate(db, ctx, clock, client_ip(request))


@router.post("/{reminder_id}/open", response_model=ReminderLink)
def open_(
    reminder_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> ReminderLink:
    return svc.open_reminder(db, ctx, clock, reminder_id, client_ip(request))


@router.post("/{reminder_id}/mark-sent", response_model=ReminderOut)
def mark_sent(
    reminder_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> ReminderOut:
    return svc.mark_sent(db, ctx, clock, reminder_id, client_ip(request))
