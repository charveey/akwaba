import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.schemas.reports import AmountReport, AuditPage, ProfitReport
from app.services import reports as svc

reports_router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_admin)])
audit_router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])


@reports_router.get("/revenue", response_model=AmountReport)
def revenue(
    group_by: Literal["month", "quarter", "year", "payment_method"] = "month",
    date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db),
) -> AmountReport:
    return svc.revenue_report(db, group_by=group_by, date_from=date_from, date_to=date_to)


@reports_router.get("/revenue-by-plan", response_model=AmountReport)
def revenue_by_plan(
    date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db),
) -> AmountReport:
    return svc.revenue_by_plan_report(db, date_from=date_from, date_to=date_to)


@reports_router.get("/expenses", response_model=AmountReport)
def expenses(
    group_by: Literal["month", "quarter", "year", "category"] = "month",
    date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db),
) -> AmountReport:
    return svc.expenses_report(db, group_by=group_by, date_from=date_from, date_to=date_to)


@reports_router.get("/profit", response_model=ProfitReport)
def profit(
    group_by: Literal["month", "quarter", "year"] = "month",
    date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db),
) -> ProfitReport:
    return svc.profit_report(db, group_by=group_by, date_from=date_from, date_to=date_to)


@audit_router.get("", response_model=AuditPage)
def audit(
    action: str | None = Query(default=None, max_length=80),
    entity_type: str | None = Query(default=None, max_length=80),
    entity_id: str | None = Query(default=None, max_length=64),
    actor_admin_id: uuid.UUID | None = None,
    date_from: date | None = None, date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> AuditPage:
    items, total = svc.list_audit(
        db, action=action, entity_type=entity_type, entity_id=entity_id, actor_admin_id=actor_admin_id,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )
    return AuditPage(items=items, total=total, limit=limit, offset=offset)
