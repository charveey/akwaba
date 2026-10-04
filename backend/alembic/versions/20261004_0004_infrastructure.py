"""infrastructure : exit node, agents, observations, protections, quarantaine, décisions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_PK = dict(server_default=sa.text("gen_random_uuid()"), nullable=False)
NOW = dict(server_default=sa.func.now(), nullable=False)
TS = sa.DateTime(timezone=True)
OPEN_Q = "status IN ('PENDING', 'EXPIRED', 'PENDING_REVOCATION', 'MANUAL_REVIEW')"


def jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "exit_nodes",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("tailscale_ip", sa.String(length=45), nullable=False),
        sa.Column("tailscale_stable_id", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", TS, **NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "agent_credentials",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("exit_node_id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", TS, **NOW),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("last_used_at", TS, nullable=True),
        sa.ForeignKeyConstraint(["exit_node_id"], ["exit_nodes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_id"),
    )
    op.create_index("ix_agent_credentials_exit_node_id", "agent_credentials", ["exit_node_id"])

    op.create_table(
        "agent_heartbeats",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("exit_node_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", TS, **NOW),
        sa.Column("agent_version", sa.String(length=32), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("tailscaled_version", sa.String(length=64), nullable=True),
        sa.Column("conntrack_ok", sa.Boolean(), nullable=False),
        sa.Column("status_ok", sa.Boolean(), nullable=False),
        sa.Column("whois_ok", sa.Boolean(), nullable=False),
        sa.Column("bytes_available", sa.Boolean(), nullable=False),
        sa.Column("spool_depth", sa.Integer(), nullable=False),
        sa.Column("conntrack_count", sa.Integer(), nullable=True),
        sa.Column("conntrack_max", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("details", jsonb(), nullable=True),
        sa.ForeignKeyConstraint(["exit_node_id"], ["exit_nodes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_heartbeats_exit_node_id_received_at", "agent_heartbeats", ["exit_node_id", "received_at"])

    op.create_table(
        "agent_batches",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("exit_node_id", sa.Uuid(), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("received_at", TS, **NOW),
        sa.Column("window_start", TS, nullable=False),
        sa.Column("window_end", TS, nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["exit_node_id"], ["exit_nodes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("batch_id"),
        sa.CheckConstraint("window_end >= window_start", name="window_order"),
        sa.CheckConstraint("sample_count >= 0", name="sample_count_non_negative"),
    )
    op.create_index("ix_agent_batches_exit_node_id_received_at", "agent_batches", ["exit_node_id", "received_at"])

    op.create_table(
        "exit_node_identities",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("exit_node_id", sa.Uuid(), nullable=False),
        sa.Column("login_name", sa.String(length=320), nullable=False),
        sa.Column("tailscale_user_id", sa.BigInteger(), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("is_sharee", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("client_identity_id", sa.Uuid(), nullable=True),
        sa.Column("first_seen_at", TS, **NOW),
        sa.Column("last_seen_at", TS, **NOW),
        sa.ForeignKeyConstraint(["exit_node_id"], ["exit_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["client_identity_id"], ["client_identities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("login_name = lower(login_name)", name="login_lowercase"),
    )
    op.create_index("ix_exit_node_identities_client_identity_id", "exit_node_identities", ["client_identity_id"])
    op.create_index(
        "uq_exit_node_identities_exit_node_id_login_name", "exit_node_identities",
        ["exit_node_id", "login_name"], unique=True,
    )

    op.create_table(
        "exit_node_peers",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("exit_node_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("stable_id", sa.String(length=64), nullable=False),
        sa.Column("tailscale_ip", sa.String(length=45), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("os", sa.String(length=40), nullable=True),
        sa.Column("is_sharee", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", TS, **NOW),
        sa.Column("last_seen_at", TS, **NOW),
        sa.ForeignKeyConstraint(["exit_node_id"], ["exit_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["identity_id"], ["exit_node_identities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exit_node_peers_identity_id", "exit_node_peers", ["identity_id"])
    op.create_index("uq_exit_node_peers_exit_node_id_stable_id", "exit_node_peers", ["exit_node_id", "stable_id"], unique=True)
    op.create_index("ix_exit_node_peers_exit_node_id_tailscale_ip", "exit_node_peers", ["exit_node_id", "tailscale_ip"])

    op.create_table(
        "exit_node_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("exit_node_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=True),
        sa.Column("peer_id", sa.Uuid(), nullable=True),
        sa.Column("tailscale_ip", sa.String(length=45), nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("resolution_status", sa.String(length=12), nullable=False),
        sa.Column("active_flows", sa.Integer(), nullable=False),
        sa.Column("new_flows", sa.Integer(), nullable=False),
        sa.Column("replied_flows", sa.Integer(), nullable=False),
        sa.Column("bytes_in", sa.BigInteger(), nullable=True),
        sa.Column("bytes_out", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], ["agent_batches.batch_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["exit_node_id"], ["exit_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["identity_id"], ["exit_node_identities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["peer_id"], ["exit_node_peers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("resolution_status IN ('RESOLVED', 'UNRESOLVED')", name="resolution_valid"),
        sa.CheckConstraint(
            "(resolution_status = 'RESOLVED' AND identity_id IS NOT NULL)"
            " OR (resolution_status = 'UNRESOLVED' AND identity_id IS NULL)",
            name="resolved_has_identity",
        ),
        sa.CheckConstraint("active_flows >= 0 AND new_flows >= 0 AND replied_flows >= 0", name="counts_non_negative"),
        sa.CheckConstraint("window_seconds > 0", name="window_positive"),
    )
    op.create_index("ix_exit_node_observations_batch_id", "exit_node_observations", ["batch_id"])
    op.create_index("ix_exit_node_observations_exit_node_id_observed_at", "exit_node_observations", ["exit_node_id", "observed_at"])
    op.create_index("ix_exit_node_observations_identity_id_observed_at", "exit_node_observations", ["identity_id", "observed_at"])

    op.create_table(
        "exit_node_sessions",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("exit_node_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("tailscale_ip", sa.String(length=45), nullable=False),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("closed_at", TS, nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("new_flows_total", sa.Integer(), nullable=False),
        sa.Column("peak_active_flows", sa.Integer(), nullable=False),
        sa.Column("bytes_in", sa.BigInteger(), nullable=True),
        sa.Column("bytes_out", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["exit_node_id"], ["exit_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["identity_id"], ["exit_node_identities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("last_seen_at >= first_seen_at", name="seen_order"),
    )
    op.create_index(
        "uq_exit_node_sessions_open", "exit_node_sessions", ["identity_id", "tailscale_ip"],
        unique=True, postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_index("ix_exit_node_sessions_identity_id_first_seen_at", "exit_node_sessions", ["identity_id", "first_seen_at"])

    op.create_table(
        "tailnet_members",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("tailscale_user_id", sa.String(length=32), nullable=False),
        sa.Column("login_name", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=True),
        sa.Column("user_type", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("is_present", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("first_synced_at", TS, **NOW),
        sa.Column("last_synced_at", TS, **NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tailscale_user_id"),
        sa.CheckConstraint("login_name = lower(login_name)", name="login_lowercase"),
    )
    op.create_index("ix_tailnet_members_login_name", "tailnet_members", ["login_name"])

    op.create_table(
        "tailnet_devices",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("tailscale_device_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("owner_login_name", sa.String(length=320), nullable=True),
        sa.Column("addresses", jsonb(), nullable=True),
        sa.Column("is_external", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("authorized", sa.Boolean(), nullable=True),
        sa.Column("last_seen_at", TS, nullable=True),
        sa.Column("is_present", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("first_synced_at", TS, **NOW),
        sa.Column("last_synced_at", TS, **NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tailscale_device_id"),
    )
    op.create_index("ix_tailnet_devices_owner_login_name", "tailnet_devices", ["owner_login_name"])

    op.create_table(
        "protected_identities",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("login_name", sa.String(length=320), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_admin_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", TS, **NOW),
        sa.Column("removed_at", TS, nullable=True),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("login_name = lower(login_name)", name="login_lowercase"),
        sa.CheckConstraint("source IN ('AUTO_MEMBER', 'AUTO_DEVICE', 'MANUAL')", name="source_valid"),
    )
    op.create_index(
        "uq_protected_identities_active", "protected_identities", ["login_name", "source"],
        unique=True, postgresql_where=sa.text("removed_at IS NULL"),
    )

    op.create_table(
        "quarantine_records",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("exit_node_identity_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("resolved_at", TS, nullable=True),
        sa.Column("resolved_by_admin_id", sa.Uuid(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("created_at", TS, **NOW),
        sa.Column("updated_at", TS, **NOW),
        sa.ForeignKeyConstraint(["exit_node_identity_id"], ["exit_node_identities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resolved_by_admin_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'AUTHORIZED', 'EXPIRED', 'PENDING_REVOCATION', 'MANUAL_REVIEW', 'DISMISSED')",
            name="status_valid",
        ),
        sa.CheckConstraint("expires_at > started_at", name="expiry_after_start"),
        sa.CheckConstraint(
            "(status IN ('AUTHORIZED', 'DISMISSED') AND resolved_at IS NOT NULL)"
            " OR (status NOT IN ('AUTHORIZED', 'DISMISSED') AND resolved_at IS NULL)",
            name="resolved_consistent",
        ),
    )
    op.create_index("ix_quarantine_records_exit_node_identity_id", "quarantine_records", ["exit_node_identity_id"])
    op.create_index(
        "uq_quarantine_records_open", "quarantine_records", ["exit_node_identity_id"],
        unique=True, postgresql_where=sa.text(OPEN_Q),
    )

    op.create_table(
        "enforcement_decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("exit_node_identity_id", sa.Uuid(), nullable=False),
        sa.Column("quarantine_record_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=10), server_default=sa.text("'dry_run'"), nullable=False),
        sa.Column("enforced", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("decided_at", TS, **NOW),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("context", jsonb(), nullable=True),
        sa.ForeignKeyConstraint(["exit_node_identity_id"], ["exit_node_identities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quarantine_record_id"], ["quarantine_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("decision IN ('WOULD_BLOCK', 'WOULD_UNBLOCK')", name="decision_valid"),
        sa.CheckConstraint("mode = 'dry_run'", name="dry_run_only"),
        sa.CheckConstraint("enforced = false", name="never_enforced"),
    )
    op.create_index("ix_enforcement_decisions_identity_decided_at", "enforcement_decisions", ["exit_node_identity_id", "decided_at"])

    op.execute(
        """
        CREATE FUNCTION append_only_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% est append-only (% interdit)', TG_TABLE_NAME, TG_OP;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforcement_decisions_no_update_delete
        BEFORE UPDATE OR DELETE ON enforcement_decisions
        FOR EACH ROW EXECUTE FUNCTION append_only_guard()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforcement_decisions_no_truncate
        BEFORE TRUNCATE ON enforcement_decisions
        FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS enforcement_decisions_no_truncate ON enforcement_decisions")
    op.execute("DROP TRIGGER IF EXISTS enforcement_decisions_no_update_delete ON enforcement_decisions")
    op.execute("DROP FUNCTION IF EXISTS append_only_guard()")
    for table in (
        "enforcement_decisions", "quarantine_records", "protected_identities", "tailnet_devices",
        "tailnet_members", "exit_node_sessions", "exit_node_observations", "exit_node_peers",
        "exit_node_identities", "agent_batches", "agent_heartbeats", "agent_credentials", "exit_nodes",
    ):
        op.drop_table(table)
