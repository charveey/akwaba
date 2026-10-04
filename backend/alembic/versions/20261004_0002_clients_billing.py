"""members, client_identities, plans, payment_methods, subscriptions, payments

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_PK = dict(server_default=sa.text("gen_random_uuid()"), nullable=False)
NOW = dict(server_default=sa.func.now(), nullable=False)
CURRENCY_OK = "currency = upper(currency) AND length(currency) = 3"


def upgrade() -> None:
    op.create_table(
        "members",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone_e164", sa.String(length=16), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), **NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("email IS NULL OR email = lower(email)", name="email_lowercase"),
        sa.CheckConstraint(r"phone_e164 IS NULL OR phone_e164 ~ '^\+[1-9][0-9]{7,14}$'", name="phone_e164_format"),
    )

    op.create_table(
        "client_identities",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=30), server_default=sa.text("'tailscale'"), nullable=False),
        sa.Column("login_name", sa.String(length=320), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), **NOW),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **NOW),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("login_name = lower(login_name)", name="login_lowercase"),
        sa.CheckConstraint(
            "(is_active AND unlinked_at IS NULL) OR (NOT is_active AND unlinked_at IS NOT NULL)",
            name="active_matches_unlinked",
        ),
    )
    op.create_index("ix_client_identities_member_id", "client_identities", ["member_id"])
    op.create_index(
        "uq_client_identities_active_login", "client_identities", ["provider", "login_name"],
        unique=True, postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("price_amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=True),
        sa.Column("is_unlimited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), **NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.CheckConstraint("price_amount >= 0", name="price_non_negative"),
        sa.CheckConstraint(CURRENCY_OK, name="currency_format"),
        sa.CheckConstraint(
            "(is_unlimited AND duration_months IS NULL) OR (NOT is_unlimited AND duration_months > 0)",
            name="duration_matches_unlimited",
        ),
    )

    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("previous_subscription_id", sa.Uuid(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("is_unlimited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("grace_period_days", sa.Integer(), nullable=False),
        sa.Column("price_amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("payment_status", sa.String(length=10), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), **NOW),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["subscription_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_subscription_id"], ["subscriptions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("payment_status IN ('PENDING', 'PAID', 'WAIVED')", name="payment_status_valid"),
        sa.CheckConstraint("expires_on IS NULL OR expires_on > starts_on", name="dates_order"),
        sa.CheckConstraint(
            "(is_unlimited AND expires_on IS NULL AND payment_status = 'WAIVED')"
            " OR (NOT is_unlimited AND expires_on IS NOT NULL)",
            name="unlimited_rules",
        ),
        sa.CheckConstraint("price_amount >= 0", name="price_non_negative"),
        sa.CheckConstraint("grace_period_days >= 0", name="grace_non_negative"),
        sa.CheckConstraint(CURRENCY_OK, name="currency_format"),
    )
    op.create_index("ix_subscriptions_member_id", "subscriptions", ["member_id"])
    op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"])
    op.create_index("ix_subscriptions_expires_on", "subscriptions", ["expires_on"])
    op.create_index(
        "uq_subscriptions_previous", "subscriptions", ["previous_subscription_id"],
        unique=True, postgresql_where=sa.text("previous_subscription_id IS NOT NULL"),
    )

    op.execute(
        """
        CREATE FUNCTION subscriptions_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'subscriptions: DELETE interdit';
            END IF;
            IF (NEW.member_id, NEW.plan_id, NEW.previous_subscription_id, NEW.starts_on, NEW.expires_on,
                NEW.is_unlimited, NEW.grace_period_days, NEW.price_amount, NEW.currency, NEW.created_at)
               IS DISTINCT FROM
               (OLD.member_id, OLD.plan_id, OLD.previous_subscription_id, OLD.starts_on, OLD.expires_on,
                OLD.is_unlimited, OLD.grace_period_days, OLD.price_amount, OLD.currency, OLD.created_at) THEN
                RAISE EXCEPTION 'subscriptions: les termes sont immuables (creer un renouvellement)';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER subscriptions_guard
        BEFORE UPDATE OR DELETE ON subscriptions
        FOR EACH ROW EXECUTE FUNCTION subscriptions_guard()
        """
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), **UUID_PK),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'CONFIRMED'"), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("recorded_by_admin_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **NOW),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_method_id"], ["payment_methods.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_admin_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
        sa.CheckConstraint(CURRENCY_OK, name="currency_format"),
        sa.CheckConstraint("status IN ('CONFIRMED', 'VOIDED')", name="status_valid"),
        sa.CheckConstraint(
            "(status = 'CONFIRMED' AND voided_at IS NULL) OR (status = 'VOIDED' AND voided_at IS NOT NULL)",
            name="void_consistent",
        ),
    )
    op.create_index("ix_payments_subscription_id", "payments", ["subscription_id"])
    op.create_index("ix_payments_paid_at", "payments", ["paid_at"])

    op.execute(
        """
        CREATE FUNCTION payments_guard() RETURNS trigger
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
    op.execute(
        """
        CREATE TRIGGER payments_guard
        BEFORE UPDATE OR DELETE ON payments
        FOR EACH ROW EXECUTE FUNCTION payments_guard()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS payments_guard ON payments")
    op.execute("DROP FUNCTION IF EXISTS payments_guard()")
    op.drop_index("ix_payments_paid_at", table_name="payments")
    op.drop_index("ix_payments_subscription_id", table_name="payments")
    op.drop_table("payments")

    op.execute("DROP TRIGGER IF EXISTS subscriptions_guard ON subscriptions")
    op.execute("DROP FUNCTION IF EXISTS subscriptions_guard()")
    op.drop_index("uq_subscriptions_previous", table_name="subscriptions")
    op.drop_index("ix_subscriptions_expires_on", table_name="subscriptions")
    op.drop_index("ix_subscriptions_plan_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_member_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_table("payment_methods")
    op.drop_table("subscription_plans")
    op.drop_index("uq_client_identities_active_login", table_name="client_identities")
    op.drop_index("ix_client_identities_member_id", table_name="client_identities")
    op.drop_table("client_identities")
    op.drop_table("members")
