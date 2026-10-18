"""observations : new_flows nullable et colonnes calculées par l'agent

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

T = "exit_node_observations"


def upgrade() -> None:
    op.alter_column(T, "new_flows", existing_type=sa.Integer(), nullable=True)
    op.add_column(T, sa.Column("total_flows", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column(T, sa.Column("unknown_age_flows", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column(T, sa.Column("consecutive_active_windows", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column(T, sa.Column("active_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column(T, sa.Column("identity_technical_error", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    for col in ("identity_technical_error", "active_confirmed", "consecutive_active_windows",
                "unknown_age_flows", "total_flows"):
        op.drop_column(T, col)
    op.execute(f"UPDATE {T} SET new_flows = 0 WHERE new_flows IS NULL")
    op.alter_column(T, "new_flows", existing_type=sa.Integer(), nullable=False)
