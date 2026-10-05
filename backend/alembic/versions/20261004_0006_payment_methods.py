"""moyens de paiement réels

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_METHODS = [
    ("WAVE", "Wave"),
    ("MOOV_MONEY", "Moov Money"),
    ("ORANGE_MONEY", "Orange Money"),
    ("PAYLIB", "Paylib"),
    ("PAYPAL", "PayPal"),
    ("DJAMO", "Djamo"),
]


def upgrade() -> None:
    methods = sa.table("payment_methods", sa.column("code", sa.String), sa.column("name", sa.String))
    op.bulk_insert(methods, [dict(code=c, name=n) for c, n in NEW_METHODS])
    # MOBILE_MONEY est remplacé par les opérateurs précis. Supprimé seulement s'il n'est pas utilisé.
    op.execute(
        """
        DELETE FROM payment_methods
        WHERE code = 'MOBILE_MONEY'
          AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.payment_method_id = payment_methods.id)
        """
    )
    op.execute("UPDATE payment_methods SET is_active = false WHERE code = 'MOBILE_MONEY'")


def downgrade() -> None:
    op.execute("UPDATE payment_methods SET is_active = true WHERE code = 'MOBILE_MONEY'")
    op.execute(
        """
        INSERT INTO payment_methods (code, name)
        SELECT 'MOBILE_MONEY', 'Mobile money'
        WHERE NOT EXISTS (SELECT 1 FROM payment_methods WHERE code = 'MOBILE_MONEY')
        """
    )
    op.execute(
        """
        DELETE FROM payment_methods
        WHERE code IN ('WAVE', 'MOOV_MONEY', 'ORANGE_MONEY', 'PAYLIB', 'PAYPAL', 'DJAMO')
          AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.payment_method_id = payment_methods.id)
        """
    )
