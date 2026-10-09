import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_clock, require_admin, require_csrf
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.billing import VoidIn
from app.schemas.finance import (
    CategoryIn, CategoryOut, CategoryUpdate, ExpenseCreate, ExpenseOut, ExpensePage,
)
from app.services import finance as svc
from app.services.auth import AuthContext

categories_router = APIRouter(prefix="/expense-categories", tags=["expenses"])
expenses_router = APIRouter(prefix="/expenses", tags=["expenses"])


@categories_router.get("", response_model=list[CategoryOut], dependencies=[Depends(require_admin)])
def list_categories(include_inactive: bool = False, db: Session = Depends(get_db)) -> list[CategoryOut]:
    return svc.list_categories(db, include_inactive=include_inactive)


@categories_router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(
    body: CategoryIn, request: Request, ctx: AuthContext = Depends(require_csrf), db: Session = Depends(get_db),
) -> CategoryOut:
    return svc.create_category(db, ctx, body, client_ip(request))


@categories_router.put("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: uuid.UUID, body: CategoryUpdate, request: Request,
    ctx: AuthContext = Depends(require_csrf), db: Session = Depends(get_db),
) -> CategoryOut:
    return svc.update_category(db, ctx, category_id, body, client_ip(request))


@expenses_router.get("", response_model=ExpensePage, dependencies=[Depends(require_admin)])
def list_expenses(
    category_id: uuid.UUID | None = None,
    status_: Literal["ACTIVE", "VOIDED"] | None = Query(default=None, alias="status"),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ExpensePage:
    items, total, total_amount = svc.list_expenses(
        db, category_id=category_id, exp_status=status_, date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )
    return ExpensePage(items=items, total=total, total_amount=total_amount, limit=limit, offset=offset)


@expenses_router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
def create_expense(
    body: ExpenseCreate, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> ExpenseOut:
    return svc.create_expense(db, ctx, clock, body, client_ip(request))


@expenses_router.get("/{expense_id}", response_model=ExpenseOut, dependencies=[Depends(require_admin)])
def read_expense(expense_id: uuid.UUID, db: Session = Depends(get_db)) -> ExpenseOut:
    return svc.get_expense(db, expense_id)


@expenses_router.post("/{expense_id}/void", response_model=ExpenseOut)
def void_expense(
    expense_id: uuid.UUID, body: VoidIn, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> ExpenseOut:
    return svc.void_expense(db, ctx, clock, expense_id, body, client_ip(request))
