import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin

_UUID_PK = dict(primary_key=True, server_default=text("gen_random_uuid()"))
_OPEN_QUARANTINE = "status IN ('PENDING', 'EXPIRED', 'PENDING_REVOCATION', 'MANUAL_REVIEW')"


class ExitNode(Base):
    __tablename__ = "exit_nodes"

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    tailscale_ip: Mapped[str] = mapped_column(String(45))
    tailscale_stable_id: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AgentCredential(Base):
    __tablename__ = "agent_credentials"

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    exit_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_nodes.id", ondelete="RESTRICT"), index=True)
    key_id: Mapped[str] = mapped_column(String(64), unique=True)
    # Secret HMAC chiffré (FIELD_ENCRYPTION_KEY). Jamais en clair.
    secret_encrypted: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]


class AgentHeartbeat(Base):
    __tablename__ = "agent_heartbeats"
    __table_args__ = (Index("ix_agent_heartbeats_exit_node_id_received_at", "exit_node_id", "received_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    exit_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_nodes.id", ondelete="RESTRICT"))
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    agent_version: Mapped[str] = mapped_column(String(32))
    protocol_version: Mapped[int]
    tailscaled_version: Mapped[str | None] = mapped_column(String(64))
    conntrack_ok: Mapped[bool]
    status_ok: Mapped[bool]
    whois_ok: Mapped[bool]
    bytes_available: Mapped[bool]
    spool_depth: Mapped[int]
    conntrack_count: Mapped[int | None]
    conntrack_max: Mapped[int | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[Any | None] = mapped_column(JSONB)


class AgentBatch(Base):
    """batch_id (UUID fourni par l'agent) = clé d'idempotence."""

    __tablename__ = "agent_batches"
    __table_args__ = (
        CheckConstraint("window_end >= window_start", name="window_order"),
        CheckConstraint("sample_count >= 0", name="sample_count_non_negative"),
        Index("ix_agent_batches_exit_node_id_received_at", "exit_node_id", "received_at"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    exit_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_nodes.id", ondelete="RESTRICT"))
    protocol_version: Mapped[int]
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    sample_count: Mapped[int]


class ExitNodeIdentity(Base):
    """Identité technique Tailscale observée. Jamais automatiquement un membre."""

    __tablename__ = "exit_node_identities"
    __table_args__ = (
        CheckConstraint("login_name = lower(login_name)", name="login_lowercase"),
        Index("uq_exit_node_identities_exit_node_id_login_name", "exit_node_id", "login_name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    exit_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_nodes.id", ondelete="RESTRICT"))
    login_name: Mapped[str] = mapped_column(String(320))
    tailscale_user_id: Mapped[int | None] = mapped_column(BigInteger)
    display_name: Mapped[str | None] = mapped_column(String(200))
    is_sharee: Mapped[bool] = mapped_column(server_default=text("false"))
    # Le pont vers le domaine clients.
    client_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("client_identities.id", ondelete="RESTRICT"), index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ExitNodePeer(Base):
    """Un appareil Tailscale (StableID) rattaché à une identité."""

    __tablename__ = "exit_node_peers"
    __table_args__ = (
        Index("uq_exit_node_peers_exit_node_id_stable_id", "exit_node_id", "stable_id", unique=True),
        Index("ix_exit_node_peers_exit_node_id_tailscale_ip", "exit_node_id", "tailscale_ip"),
    )

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    exit_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_nodes.id", ondelete="RESTRICT"))
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_node_identities.id", ondelete="RESTRICT"), index=True)
    stable_id: Mapped[str] = mapped_column(String(64))
    tailscale_ip: Mapped[str] = mapped_column(String(45))
    hostname: Mapped[str | None] = mapped_column(String(255))
    os: Mapped[str | None] = mapped_column(String(40))
    is_sharee: Mapped[bool]
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ExitNodeObservation(Base):
    """Échantillon agrégé. Aucune IP ni port de destination (jamais conservés)."""

    __tablename__ = "exit_node_observations"
    __table_args__ = (
        CheckConstraint("resolution_status IN ('RESOLVED', 'UNRESOLVED')", name="resolution_valid"),
        CheckConstraint(
            "(resolution_status = 'RESOLVED' AND identity_id IS NOT NULL)"
            " OR (resolution_status = 'UNRESOLVED' AND identity_id IS NULL)",
            name="resolved_has_identity",
        ),
        CheckConstraint("active_flows >= 0 AND new_flows >= 0 AND replied_flows >= 0", name="counts_non_negative"),
        CheckConstraint("window_seconds > 0", name="window_positive"),
        Index("ix_exit_node_observations_exit_node_id_observed_at", "exit_node_id", "observed_at"),
        Index("ix_exit_node_observations_identity_id_observed_at", "identity_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_batches.batch_id", ondelete="RESTRICT"), index=True)
    exit_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_nodes.id", ondelete="RESTRICT"))
    identity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("exit_node_identities.id", ondelete="RESTRICT"))
    peer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("exit_node_peers.id", ondelete="RESTRICT"))
    tailscale_ip: Mapped[str] = mapped_column(String(45))
    observed_at: Mapped[datetime]
    window_seconds: Mapped[int]
    resolution_status: Mapped[str] = mapped_column(String(12))
    active_flows: Mapped[int]
    new_flows: Mapped[int]
    replied_flows: Mapped[int]
    # NULL = volume indisponible (jamais 0 artificiel).
    bytes_in: Mapped[int | None] = mapped_column(BigInteger)
    bytes_out: Mapped[int | None] = mapped_column(BigInteger)


class ExitNodeSession(Base):
    __tablename__ = "exit_node_sessions"
    __table_args__ = (
        CheckConstraint("last_seen_at >= first_seen_at", name="seen_order"),
        Index(
            "uq_exit_node_sessions_open", "identity_id", "tailscale_ip",
            unique=True, postgresql_where=text("closed_at IS NULL"),
        ),
        Index("ix_exit_node_sessions_identity_id_first_seen_at", "identity_id", "first_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    exit_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_nodes.id", ondelete="RESTRICT"))
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_node_identities.id", ondelete="RESTRICT"))
    tailscale_ip: Mapped[str] = mapped_column(String(45))
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    sample_count: Mapped[int]
    new_flows_total: Mapped[int]
    peak_active_flows: Mapped[int]
    bytes_in: Mapped[int | None] = mapped_column(BigInteger)
    bytes_out: Mapped[int | None] = mapped_column(BigInteger)


class TailnetMember(Base):
    """Copie en lecture seule de /users (REST Tailscale)."""

    __tablename__ = "tailnet_members"
    __table_args__ = (CheckConstraint("login_name = lower(login_name)", name="login_lowercase"),)

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    tailscale_user_id: Mapped[str] = mapped_column(String(32), unique=True)
    login_name: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(30))
    user_type: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str | None] = mapped_column(String(30))
    is_present: Mapped[bool] = mapped_column(server_default=text("true"))
    first_synced_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_synced_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TailnetDevice(Base):
    """Copie en lecture seule de /devices (REST Tailscale)."""

    __tablename__ = "tailnet_devices"

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    tailscale_device_id: Mapped[str] = mapped_column(String(64), unique=True)
    node_id: Mapped[str | None] = mapped_column(String(64))
    hostname: Mapped[str | None] = mapped_column(String(255))
    owner_login_name: Mapped[str | None] = mapped_column(String(320), index=True)
    addresses: Mapped[Any | None] = mapped_column(JSONB)
    is_external: Mapped[bool] = mapped_column(server_default=text("false"))
    authorized: Mapped[bool | None]
    last_seen_at: Mapped[datetime | None]
    is_present: Mapped[bool] = mapped_column(server_default=text("true"))
    first_synced_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_synced_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProtectedIdentity(Base):
    __tablename__ = "protected_identities"
    __table_args__ = (
        CheckConstraint("login_name = lower(login_name)", name="login_lowercase"),
        CheckConstraint("source IN ('AUTO_MEMBER', 'AUTO_DEVICE', 'MANUAL')", name="source_valid"),
        Index(
            "uq_protected_identities_active", "login_name", "source",
            unique=True, postgresql_where=text("removed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    login_name: Mapped[str] = mapped_column(String(320))
    source: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    removed_at: Mapped[datetime | None]


class QuarantineRecord(TimestampMixin, Base):
    __tablename__ = "quarantine_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'AUTHORIZED', 'EXPIRED', 'PENDING_REVOCATION', 'MANUAL_REVIEW', 'DISMISSED')",
            name="status_valid",
        ),
        CheckConstraint("expires_at > started_at", name="expiry_after_start"),
        CheckConstraint(
            "(status IN ('AUTHORIZED', 'DISMISSED') AND resolved_at IS NOT NULL)"
            " OR (status NOT IN ('AUTHORIZED', 'DISMISSED') AND resolved_at IS NULL)",
            name="resolved_consistent",
        ),
        # Au plus un dossier ouvert par identité.
        Index(
            "uq_quarantine_records_open", "exit_node_identity_id",
            unique=True, postgresql_where=text(_OPEN_QUARANTINE),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(**_UUID_PK)
    exit_node_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exit_node_identities.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    resolved_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class EnforcementDecision(Base):
    """Décision calculée en dry-run. Append-only ; jamais appliquée en V1."""

    __tablename__ = "enforcement_decisions"
    __table_args__ = (
        CheckConstraint("decision IN ('WOULD_BLOCK', 'WOULD_UNBLOCK')", name="decision_valid"),
        CheckConstraint("mode = 'dry_run'", name="dry_run_only"),
        CheckConstraint("enforced = false", name="never_enforced"),
        Index("ix_enforcement_decisions_identity_decided_at", "exit_node_identity_id", "decided_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    exit_node_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exit_node_identities.id", ondelete="RESTRICT"))
    quarantine_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("quarantine_records.id", ondelete="RESTRICT")
    )
    decision: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(10), server_default=text("'dry_run'"))
    enforced: Mapped[bool] = mapped_column(server_default=text("false"))
    decided_at: Mapped[datetime] = mapped_column(server_default=func.now())
    reason: Mapped[str | None] = mapped_column(String(255))
    context: Mapped[Any | None] = mapped_column(JSONB)
