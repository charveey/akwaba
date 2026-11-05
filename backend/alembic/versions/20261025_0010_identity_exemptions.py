"""exemptions d'identité : autorisation explicite d'un administrateur, terminée par ended_at

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "identity_exemptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("exit_node_identity_id", sa.Uuid(), nullable=False),
        sa.Column("quarantine_record_id", sa.Uuid(), nullable=True),
        sa.Column("granted_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("granted_by_admin_id", sa.Uuid(), nullable=True),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("ended_by_admin_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["exit_node_identity_id"], ["exit_node_identities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quarantine_record_id"], ["quarantine_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["granted_by_admin_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ended_by_admin_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("ended_at IS NULL OR ended_at >= granted_at", name="ended_after_granted"),
    )
    op.create_index(
        "uq_identity_exemptions_active", "identity_exemptions", ["exit_node_identity_id"],
        unique=True, postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_identity_exemptions_active", table_name="identity_exemptions")
    op.drop_table("identity_exemptions")
