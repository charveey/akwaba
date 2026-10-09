import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ExpenseCategory(TimestampMixin, Base):
    __tablename__ = "expense_categories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String(80), unique=True)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))


class Expense(Base):
    """Jamais supprimée : on l'annule (status VOIDED, irréversible). Montants en francs CFA entiers."""

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency = 'XOF'", name="xof_only"),
        CheckConstraint("status IN ('ACTIVE', 'VOIDED')", name="status_valid"),
        CheckConstraint(
            "(status = 'ACTIVE' AND voided_at IS NULL AND void_reason IS NULL)"
            " OR (status = 'VOIDED' AND voided_at IS NOT NULL AND void_reason IS NOT NULL)",
            name="void_consistent",
        ),
        Index("ix_expenses_expense_date", "expense_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expense_categories.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3), server_default=text("'XOF'"))
    expense_date: Mapped[date]
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), server_default=text("'ACTIVE'"))
    voided_at: Mapped[datetime | None]
    void_reason: Mapped[str | None] = mapped_column(Text)
    recorded_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
