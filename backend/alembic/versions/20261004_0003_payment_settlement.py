"""règlement XOF des paiements

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("settled_amount", sa.Integer(), nullable=True))
    op.add_column("payments", sa.Column("settled_currency", sa.String(length=3), nullable=True))
    op.add_column("payments", sa.Column("fx_rate", sa.Numeric(12, 6), nullable=True))
    op.create_check_constraint(
        "settled_required", "payments",
        "currency = 'XOF' OR (settled_amount IS NOT NULL AND settled_currency = 'XOF' AND fx_rate IS NOT NULL)",
    )
    op.create_check_constraint(
        "settled_consistent", "payments",
        "(settled_amount IS NULL AND settled_currency IS NULL AND fx_rate IS NULL)"
        " OR (settled_amount > 0 AND settled_currency = 'XOF' AND fx_rate > 0)",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION payments_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'payments: DELETE interdit (annuler via status VOIDED)';
            END IF;
            IF (NEW.subscription_id, NEW.payment_method_id, NEW.amount, NEW.currency, NEW.paid_at,
                NEW.recorded_by_admin_id, NEW.created_at, NEW.settled_amount, NEW.settled_currency, NEW.fx_rate)
               IS DISTINCT FROM
               (OLD.subscription_id, OLD.payment_method_id, OLD.amount, OLD.currency, OLD.paid_at,
                OLD.recorded_by_admin_id, OLD.created_at, OLD.settled_amount, OLD.settled_currency, OLD.fx_rate) THEN
                RAISE EXCEPTION 'payments: montant, devise, date, reglement et rattachement sont immuables';
            END IF;
            IF OLD.status = 'VOIDED' AND NEW.status <> 'VOIDED' THEN
                RAISE EXCEPTION 'payments: annulation irreversible';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION payments_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'payments: DELETE interdit (annuler via status VOIDED)';
            END IF;
            IF (NEW.subscription_id, NEW.payment_method_id, NEW.amount, NEW.currency, NEW.paid_at,
                NEW.recorded_by_admin_id, NEW.created_at)
               IS DISTINCT FROM
               (OLD.subscription_id, OLD.payment_method_id, OLD.amount, OLD.currency, OLD.paid_at,
                OLD.recorded_by_admin_id, OLD.created_at) THEN
                RAISE EXCEPTION 'payments: montant, devise, date et rattachement sont immuables';
            END IF;
            IF OLD.status = 'VOIDED' AND NEW.status <> 'VOIDED' THEN
                RAISE EXCEPTION 'payments: annulation irreversible';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.drop_constraint("settled_consistent", "payments", type_="check")
    op.drop_constraint("settled_required", "payments", type_="check")
    op.drop_column("payments", "fx_rate")
    op.drop_column("payments", "settled_currency")
    op.drop_column("payments", "settled_amount")
