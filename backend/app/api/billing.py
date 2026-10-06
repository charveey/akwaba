import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_clock, require_admin, require_csrf
from app.core.config import get_settings
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.billing import (
    PaymentCreate, PaymentMethodOut, PaymentOut, PaymentPage, PlanOut, RenewIn, SubscriptionCreate,
    SubscriptionOut, SubscriptionPage, VoidIn,
)
from app.services import billing as svc
from app.services.auth import AuthContext

reference_router = APIRouter(tags=["reference"])
subscriptions_router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])
payments_router = APIRouter(prefix="/payments", tags=["payments"])


@reference_router.get("/plans", response_model=list[PlanOut], dependencies=[Depends(require_admin)])
def plans(db: Session = Depends(get_db)) -> list[PlanOut]:
    return svc.list_plans(db)


@reference_router.get("/payment-methods", response_model=list[PaymentMethodOut], dependencies=[Depends(require_admin)])
def payment_methods(db: Session = Depends(get_db)) -> list[PaymentMethodOut]:
    return svc.list_payment_methods(db)


@subscriptions_router.get("", response_model=SubscriptionPage, dependencies=[Depends(require_admin)])
def list_subscriptions(
    member_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> SubscriptionPage:
    items, total = svc.list_subscriptions(db, clock, member_id=member_id, limit=limit, offset=offset)
    return SubscriptionPage(items=items, total=total, limit=limit, offset=offset)


@subscriptions_router.post("", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
def create_subscription(
    body: SubscriptionCreate, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> SubscriptionOut:
    return svc.create_subscription(db, ctx, clock, get_settings(), body, client_ip(request))


@subscriptions_router.get("/{sub_id}", response_model=SubscriptionOut, dependencies=[Depends(require_admin)])
def read_subscription(
    sub_id: uuid.UUID, db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> SubscriptionOut:
    return svc.get_subscription(db, clock, sub_id)


@subscriptions_router.post("/{sub_id}/renew", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
def renew(
    sub_id: uuid.UUID, body: RenewIn, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> SubscriptionOut:
    return svc.renew_subscription(db, ctx, clock, get_settings(), sub_id, body, client_ip(request))


@subscriptions_router.post("/{sub_id}/cancel", response_model=SubscriptionOut)
def cancel(
    sub_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> SubscriptionOut:
    return svc.cancel_subscription(db, ctx, clock, sub_id, client_ip(request))


@payments_router.get("", response_model=PaymentPage, dependencies=[Depends(require_admin)])
def list_payments(
    subscription_id: uuid.UUID | None = None,
    member_id: uuid.UUID | None = None,
    status_: Literal["CONFIRMED", "VOIDED"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> PaymentPage:
    items, total = svc.list_payments(
        db, subscription_id=subscription_id, member_id=member_id, pay_status=status_, limit=limit, offset=offset,
    )
    return PaymentPage(items=items, total=total, limit=limit, offset=offset)


@payments_router.post("", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def create_payment(
    body: PaymentCreate, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> PaymentOut:
    return svc.record_payment(db, ctx, clock, body, client_ip(request))


@payments_router.post("/{payment_id}/void", response_model=PaymentOut)
def void(
    payment_id: uuid.UUID, body: VoidIn, request: Request, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> PaymentOut:
    return svc.void_payment(db, ctx, clock, payment_id, body, client_ip(request))
