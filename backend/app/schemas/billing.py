import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class PaymentInfo(BaseModel):
    """Le montant n'est jamais saisi : il vaut le prix de l'abonnement."""

    model_config = ConfigDict(extra="forbid")

    payment_method_id: uuid.UUID
    paid_at: AwareDatetime | None = None
    reference: str | None = Field(default=None, max_length=120)
    # Montant réellement reçu en XOF (optionnel : sinon conversion à parité).
    settled_amount: int | None = Field(default=None, gt=0)

    @field_validator("reference", mode="before")
    @classmethod
    def _reference(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("doit être une chaîne")
        return v.strip() or None


class PaymentCreate(PaymentInfo):
    subscription_id: uuid.UUID


class SubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: uuid.UUID
    plan_id: uuid.UUID
    starts_on: date | None = None
    payment: PaymentInfo | None = None


class RenewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: uuid.UUID | None = None
    payment: PaymentInfo


class VoidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("le motif est obligatoire")
        return v.strip()


class PaymentOut(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    payment_method_code: str
    amount: int
    currency: str
    settled_amount: int | None
    settled_currency: str | None
    fx_rate: Decimal | None
    paid_at: datetime
    status: str
    reference: str | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime


class PaymentPage(BaseModel):
    items: list[PaymentOut]
    total: int
    limit: int
    offset: int


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID
    plan_code: str
    plan_name: str
    previous_subscription_id: uuid.UUID | None
    starts_on: date
    expires_on: date | None
    display_end: date | None
    grace_end: date | None
    is_unlimited: bool
    grace_period_days: int
    price_amount: int
    currency: str
    payment_status: str
    canceled_at: datetime | None
    suspended_at: datetime | None
    access_status: str
    renewal_status: str
    created_at: datetime
    payments: list[PaymentOut]


class SubscriptionPage(BaseModel):
    items: list[SubscriptionOut]
    total: int
    limit: int
    offset: int


class PlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    price_amount: int
    currency: str
    price_xof: int | None
    duration_months: int | None
    is_unlimited: bool


class PaymentMethodOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
