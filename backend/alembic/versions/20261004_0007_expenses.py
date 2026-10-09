"""catégories de dépenses (avec données initiales) et dépenses

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_PK = dict(server_default=sa.text("gen_random_uuid()"), nullable=False)
NOW = dict(server_default=sa.func.now(), nullable=False)
TS = sa.DateTime(timezone=True)

CATEGORIES = [
    ("Hébergement", 10),
    ("Domaines et logiciels", 20),
    ("Frais de paiement", 30),
    ("Marketing", 40),
    ("Autre", 50),
]


def upgrade() -> None:
    op.create_table(
        "expense_categories",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", TS, **NOW),
        sa.Column("updated_at", TS, **NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    cats = sa.table("expense_categories", sa.column("name", sa.String), sa.column("sort_order", sa.Integer))
    op.bulk_insert(cats, [dict(name=n, sort_order=o) for n, o in CATEGORIES])

    op.create_table(
        "expenses",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'XOF'"), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("recorded_by_admin_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", TS, **NOW),
        sa.ForeignKeyConstraint(["category_id"], ["expense_categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_admin_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
        sa.CheckConstraint("currency = 'XOF'", name="xof_only"),
        sa.CheckConstraint("status IN ('ACTIVE', 'VOIDED')", name="status_valid"),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND voided_at IS NULL AND void_reason IS NULL)"
            " OR (status = 'VOIDED' AND voided_at IS NOT NULL AND void_reason IS NOT NULL)",
            name="void_consistent",
        ),
    )
    op.create_index("ix_expenses_category_id", "expenses", ["category_id"])
    op.create_index("ix_expenses_expense_date", "expenses", ["expense_date"])

    op.execute(
        """
        CREATE FUNCTION expenses_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'expenses: DELETE interdit (annuler via status VOIDED)';
            END IF;
            IF (NEW.category_id, NEW.amount, NEW.currency, NEW.expense_date, NEW.description,
                NEW.recorded_by_admin_id, NEW.created_at)
               IS DISTINCT FROM
               (OLD.category_id, OLD.amount, OLD.currency, OLD.expense_date, OLD.description,
                OLD.recorded_by_admin_id, OLD.created_at) THEN
                RAISE EXCEPTION 'expenses: les champs sont immuables (annuler puis ressaisir)';
            END IF;
            IF OLD.status = 'VOIDED' AND NEW.status <> 'VOIDED' THEN
                RAISE EXCEPTION 'expenses: annulation irreversible';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER expenses_guard
        BEFORE UPDATE OR DELETE ON expenses
        FOR EACH ROW EXECUTE FUNCTION expenses_guard()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS expenses_guard ON expenses")
    op.execute("DROP FUNCTION IF EXISTS expenses_guard()")
    op.drop_index("ix_expenses_expense_date", table_name="expenses")
    op.drop_index("ix_expenses_category_id", table_name="expenses")
    op.drop_table("expenses")
    op.drop_table("expense_categories")
