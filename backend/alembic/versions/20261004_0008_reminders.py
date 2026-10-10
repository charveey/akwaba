"""relances WhatsApp

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("offset_days", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("opened_at", TS, nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subscription_id", "offset_days", name="uq_reminders_subscription_id"),
        sa.CheckConstraint("offset_days IN (-7, -3, -2, -1, 0, 1)", name="offset_valid"),
        sa.CheckConstraint("status IN ('PENDING', 'OPENED', 'SENT', 'CANCELED')", name="status_valid"),
        sa.CheckConstraint(r"phone_e164 ~ '^\+[1-9][0-9]{7,14}$'", name="phone_e164_format"),
        sa.CheckConstraint(
            "(status = 'PENDING' AND opened_at IS NULL AND sent_at IS NULL)"
            " OR (status = 'OPENED' AND opened_at IS NOT NULL AND sent_at IS NULL)"
            " OR (status = 'SENT' AND sent_at IS NOT NULL)"
            " OR (status = 'CANCELED' AND sent_at IS NULL)",
            name="timestamps_consistent",
        ),
    )
    op.create_index("ix_reminders_subscription_id", "reminders", ["subscription_id"])
    op.create_index("ix_reminders_member_id", "reminders", ["member_id"])
    op.create_index("ix_reminders_due_date", "reminders", ["due_date"])


def downgrade() -> None:
    op.drop_index("ix_reminders_due_date", table_name="reminders")
    op.drop_index("ix_reminders_member_id", table_name="reminders")
    op.drop_index("ix_reminders_subscription_id", table_name="reminders")
    op.drop_table("reminders")
