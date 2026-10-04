import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.admin import AdminUser  # noqa: F401  (résolution de la clé étrangère)
from app.models.mixins import TimestampMixin

_CURRENCY_OK = "currency = upper(currency) AND length(currency) = 3"


class SubscriptionPlan(TimestampMixin, Base):
    __tablename__ = "subscription_plans"
    __table_args__ = (
        CheckConstraint("price_amount >= 0", name="price_non_negative"),
        CheckConstraint(_CURRENCY_OK, name="currency_format"),
        CheckConstraint(
            "(is_unlimited AND duration_months IS NULL) OR (NOT is_unlimited AND duration_months > 0)",
            name="duration_matches_unlimited",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    # Unités mineures de la devise (EUR : centimes, XOF : francs).
    price_amount: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    duration_months: Mapped[int | None]
    is_unlimited: Mapped[bool] = mapped_column(server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Subscription(TimestampMixin, Base):
    """Immuable : un renouvellement crée une nouvelle ligne (voir triggers dans la migration)."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint("payment_status IN ('PENDING', 'PAID', 'WAIVED')", name="payment_status_valid"),
        CheckConstraint("expires_on IS NULL OR expires_on > starts_on", name="dates_order"),
        CheckConstraint(
            "(is_unlimited AND expires_on IS NULL AND payment_status = 'WAIVED')"
            " OR (NOT is_unlimited AND expires_on IS NOT NULL)",
            name="unlimited_rules",
        ),
        CheckConstraint("price_amount >= 0", name="price_non_negative"),
        CheckConstraint("grace_period_days >= 0", name="grace_non_negative"),
        CheckConstraint(_CURRENCY_OK, name="currency_format"),
        # Un abonnement n'est renouvelé qu'une seule fois : la chaîne reste linéaire.
        Index(
            "uq_subscriptions_previous",
            "previous_subscription_id",
            unique=True,
            postgresql_where=text("previous_subscription_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscription_plans.id", ondelete="RESTRICT"), index=True)
    previous_subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="RESTRICT")
    )
    starts_on: Mapped[date]  # inclusif
    expires_on: Mapped[date | None] = mapped_column(index=True)  # exclusif ; NULL = illimité
    is_unlimited: Mapped[bool] = mapped_column(server_default=text("false"))
    grace_period_days: Mapped[int]
    # Prix convenu au moment de la souscription (copie : le prix du plan peut changer).
    price_amount: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    payment_status: Mapped[str] = mapped_column(String(10))
    canceled_at: Mapped[datetime | None]
    suspended_at: Mapped[datetime | None]


class Payment(Base):
    """Jamais supprimé : on l'annule (status VOIDED, irréversible)."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(_CURRENCY_OK, name="currency_format"),
        CheckConstraint("status IN ('CONFIRMED', 'VOIDED')", name="status_valid"),
        CheckConstraint(
            "(status = 'CONFIRMED' AND voided_at IS NULL) OR (status = 'VOIDED' AND voided_at IS NOT NULL)",
            name="void_consistent",
        ),
        CheckConstraint(
            "currency = 'XOF' OR (settled_amount IS NOT NULL AND settled_currency = 'XOF' AND fx_rate IS NOT NULL)",
            name="settled_required",
        ),
        CheckConstraint(
            "(settled_amount IS NULL AND settled_currency IS NULL AND fx_rate IS NULL)"
            " OR (settled_amount > 0 AND settled_currency = 'XOF' AND fx_rate > 0)",
            name="settled_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="RESTRICT"), index=True
    )
    payment_method_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_methods.id", ondelete="RESTRICT"))
    amount: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    # Montant réellement reçu en XOF (immuable). Obligatoire si le paiement n'est pas en XOF.
    settled_amount: Mapped[int | None]
    settled_currency: Mapped[str | None] = mapped_column(String(3))
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    paid_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(10), server_default=text("'CONFIRMED'"))
    reference: Mapped[str | None] = mapped_column(String(120))
    voided_at: Mapped[datetime | None]
    void_reason: Mapped[str | None] = mapped_column(Text)
    recorded_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
