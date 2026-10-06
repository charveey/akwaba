"""données de référence : plans et moyens de paiement

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAN_CODES = ("M1", "M3", "M6", "M12", "PLATINUM")
METHOD_CODES = ("CASH", "MOBILE_MONEY", "BANK_TRANSFER")


def upgrade() -> None:
    plans = sa.table(
        "subscription_plans",
        sa.column("code", sa.String), sa.column("name", sa.String), sa.column("price_amount", sa.Integer),
        sa.column("currency", sa.String), sa.column("duration_months", sa.Integer),
        sa.column("is_unlimited", sa.Boolean), sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(plans, [
        dict(code="M1", name="1 mois", price_amount=499, currency="EUR", duration_months=1, is_unlimited=False, sort_order=10),
        dict(code="M3", name="3 mois", price_amount=1299, currency="EUR", duration_months=3, is_unlimited=False, sort_order=20),
        dict(code="M6", name="6 mois", price_amount=2599, currency="EUR", duration_months=6, is_unlimited=False, sort_order=30),
        dict(code="M12", name="12 mois", price_amount=5399, currency="EUR", duration_months=12, is_unlimited=False, sort_order=40),
        dict(code="PLATINUM", name="Platinum", price_amount=0, currency="EUR", duration_months=None, is_unlimited=True, sort_order=50),
    ])
    methods = sa.table("payment_methods", sa.column("code", sa.String), sa.column("name", sa.String))
    op.bulk_insert(methods, [
        dict(code="CASH", name="Espèces"),
        dict(code="MOBILE_MONEY", name="Mobile money"),
        dict(code="BANK_TRANSFER", name="Virement bancaire"),
    ])


def downgrade() -> None:
    # Les référentiels ne sont retirés que s'ils ne sont pas utilisés.
    op.execute(
        """
        DELETE FROM payment_methods
        WHERE code IN ('CASH', 'MOBILE_MONEY', 'BANK_TRANSFER')
          AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.payment_method_id = payment_methods.id)
        """
    )
    op.execute(
        """
        DELETE FROM subscription_plans
        WHERE code IN ('M1', 'M3', 'M6', 'M12', 'PLATINUM')
          AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.plan_id = subscription_plans.id)
        """
    )
