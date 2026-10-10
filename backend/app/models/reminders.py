import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("subscription_id", "offset_days", name="uq_reminders_subscription_id"),
        CheckConstraint("offset_days IN (-7, -3, -2, -1, 0, 1)", name="offset_valid"),
        CheckConstraint("status IN ('PENDING', 'OPENED', 'SENT', 'CANCELED')", name="status_valid"),
        CheckConstraint(r"phone_e164 ~ '^\+[1-9][0-9]{7,14}$'", name="phone_e164_format"),
        CheckConstraint(
            "(status = 'PENDING' AND opened_at IS NULL AND sent_at IS NULL)"
            " OR (status = 'OPENED' AND opened_at IS NOT NULL AND sent_at IS NULL)"
            " OR (status = 'SENT' AND sent_at IS NOT NULL)"
            " OR (status = 'CANCELED' AND sent_at IS NULL)",
            name="timestamps_consistent",
        ),
        Index("ix_reminders_due_date", "due_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="RESTRICT"), index=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True)
    offset_days: Mapped[int]
    due_date: Mapped[date]
    phone_e164: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), server_default=text("'PENDING'"))
    opened_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
