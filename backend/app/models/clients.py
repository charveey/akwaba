import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class Member(TimestampMixin, Base):
    """Le client métier. Peut avoir plusieurs identités techniques."""

    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint("email IS NULL OR email = lower(email)", name="email_lowercase"),
        CheckConstraint(r"phone_e164 IS NULL OR phone_e164 ~ '^\+[1-9][0-9]{7,14}$'", name="phone_e164_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    # Format E.164 avec "+". Le lien wa.me utilise le numéro sans "+".
    phone_e164: Mapped[str | None] = mapped_column(String(16))
    country_code: Mapped[str | None] = mapped_column(String(2))
    notes: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None]


class ClientIdentity(Base):
    """Identité technique (login Tailscale) appartenant à un membre."""

    __tablename__ = "client_identities"
    __table_args__ = (
        CheckConstraint("login_name = lower(login_name)", name="login_lowercase"),
        CheckConstraint(
            "(is_active AND unlinked_at IS NULL) OR (NOT is_active AND unlinked_at IS NOT NULL)",
            name="active_matches_unlinked",
        ),
        # Un login actif ne peut appartenir qu'à un seul membre.
        Index(
            "uq_client_identities_active_login",
            "provider",
            "login_name",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True)
    provider: Mapped[str] = mapped_column(String(30), server_default=text("'tailscale'"))
    login_name: Mapped[str] = mapped_column(String(320))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    linked_at: Mapped[datetime] = mapped_column(server_default=func.now())
    unlinked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
