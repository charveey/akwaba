"""noms affichés des formules : Bronze, Silver, Gold, Diamond (Phase 11b)

Données uniquement : codes (M1, M3, M6, M12, PLATINUM), durées et prix inchangés.
Un nom déjà modifié à la main n'est jamais écrasé (dans un sens comme dans l'autre).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# code -> (ancien nom, nouveau nom)
RENAMES = {
    "M1": ("1 mois", "Bronze"),
    "M3": ("3 mois", "Silver"),
    "M6": ("6 mois", "Gold"),
    "M12": ("12 mois", "Diamond"),
}

_UPDATE = sa.text(
    "UPDATE subscription_plans SET name = :new, updated_at = now() WHERE code = :code AND name = :old"
)


def upgrade() -> None:
    for code, (old, new) in RENAMES.items():
        op.execute(_UPDATE.bindparams(code=code, old=old, new=new))


def downgrade() -> None:
    for code, (old, new) in RENAMES.items():
        op.execute(_UPDATE.bindparams(code=code, old=new, new=old))
