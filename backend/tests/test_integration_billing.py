"""Intégration lot 2b : contraintes clients / abonnements / paiements sur PostgreSQL réel."""
import os
import pathlib
import subprocess
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL non défini")
BACKEND = pathlib.Path(__file__).resolve().parent.parent


def alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": URL}
    try:
        subprocess.run(["alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        pytest.fail(f"alembic {' '.join(args)} a échoué:\n{exc.stderr}")


@pytest.fixture(scope="module")
def engine():
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    eng = create_engine(URL)
    yield eng
    eng.dispose()
    alembic("downgrade", "base")


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def member(c):
    return c.execute(text("INSERT INTO members (full_name) VALUES ('M') RETURNING id")).scalar_one()


def plan(c, unlimited=False):
    return c.execute(
        text(
            "INSERT INTO subscription_plans (code, name, price_amount, currency, duration_months, is_unlimited) "
            "VALUES (:c, 'P', :p, 'EUR', :d, :u) RETURNING id"
        ),
        {"c": f"p-{_uid()}", "p": 0 if unlimited else 499, "d": None if unlimited else 1, "u": unlimited},
    ).scalar_one()


def sub(c, member_id, plan_id, *, starts=date(2026, 1, 17), expires=date(2026, 2, 17),
        unlimited=False, pay="PENDING", prev=None):
    return c.execute(
        text(
            "INSERT INTO subscriptions (member_id, plan_id, previous_subscription_id, starts_on, expires_on, "
            "is_unlimited, grace_period_days, price_amount, currency, payment_status) "
            "VALUES (:m, :p, :prev, :s, :e, :u, 3, 499, 'EUR', :ps) RETURNING id"
        ),
        {"m": member_id, "p": plan_id, "prev": prev, "s": starts, "e": expires, "u": unlimited, "ps": pay},
    ).scalar_one()


def payment(c, sub_id):
    method = c.execute(
        text("INSERT INTO payment_methods (code, name) VALUES (:c, 'M') RETURNING id"), {"c": f"m-{_uid()}"}
    ).scalar_one()
    return c.execute(
        text(
            "INSERT INTO payments (subscription_id, payment_method_id, amount, currency, paid_at, "
            "settled_amount, settled_currency, fx_rate) "
            "VALUES (:s, :m, 499, 'EUR', now(), 3273, 'XOF', 655.957) RETURNING id"
        ),
        {"s": sub_id, "m": method},
    ).scalar_one()


def identity(c, member_id, login, active=True):
    c.execute(
        text(
            "INSERT INTO client_identities (member_id, login_name, is_active, unlinked_at) "
            "VALUES (:m, :l, :a, CASE WHEN :a THEN NULL ELSE now() END)"
        ),
        {"m": member_id, "l": login, "a": active},
    )


def test_tables_exist(engine):
    with engine.connect() as c:
        names = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
    assert {"members", "client_identities", "subscription_plans", "payment_methods",
            "subscriptions", "payments"} <= names


def test_models_and_migrations_are_in_sync(engine):
    alembic("check")


def test_active_login_belongs_to_one_member(engine):
    login = f"{_uid()}@example.test"
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            identity(c, member(c), login)
            identity(c, member(c), login)


def test_login_can_be_reused_after_unlink(engine):
    login = f"{_uid()}@example.test"
    with engine.begin() as c:
        first = member(c)
        identity(c, first, login, active=False)
        identity(c, member(c), login)


def test_inactive_identity_requires_unlinked_at(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(
                text("INSERT INTO client_identities (member_id, login_name, is_active) VALUES (:m, :l, false)"),
                {"m": member(c), "l": f"{_uid()}@example.test"},
            )


def test_member_phone_must_be_e164(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO members (full_name, phone_e164) VALUES ('X', '0102030405')"))
    with engine.begin() as c:
        c.execute(text("INSERT INTO members (full_name, phone_e164) VALUES ('X', '+2250102030405')"))


def test_unlimited_plan_cannot_have_duration(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO subscription_plans (code, name, price_amount, currency, duration_months, is_unlimited) "
                "VALUES (:c, 'P', 0, 'EUR', 12, true)"), {"c": f"p-{_uid()}"})


def test_unlimited_subscription_must_be_waived_without_expiry(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            sub(c, member(c), plan(c, True), expires=None, unlimited=True, pay="PENDING")
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            sub(c, member(c), plan(c, True), unlimited=True, pay="WAIVED")
    with engine.begin() as c:
        sub(c, member(c), plan(c, True), expires=None, unlimited=True, pay="WAIVED")


def test_limited_subscription_requires_expiry(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            sub(c, member(c), plan(c), expires=None)


def test_expiry_must_be_after_start(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            sub(c, member(c), plan(c), starts=date(2026, 1, 17), expires=date(2026, 1, 17))


def test_a_subscription_is_renewed_only_once(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            m, p = member(c), plan(c)
            first = sub(c, m, p)
            sub(c, m, p, starts=date(2026, 2, 17), expires=date(2026, 3, 17), prev=first)
            sub(c, m, p, starts=date(2026, 2, 17), expires=date(2026, 3, 17), prev=first)


def test_subscription_terms_are_immutable(engine):
    with engine.begin() as c:
        sid = sub(c, member(c), plan(c))
    with pytest.raises(DBAPIError, match="immuables"):
        with engine.begin() as c:
            c.execute(text("UPDATE subscriptions SET price_amount = 1 WHERE id = :i"), {"i": sid})
    with pytest.raises(DBAPIError, match="immuables"):
        with engine.begin() as c:
            c.execute(text("UPDATE subscriptions SET expires_on = '2027-01-01' WHERE id = :i"), {"i": sid})


def test_payment_status_of_subscription_can_change(engine):
    with engine.begin() as c:
        sid = sub(c, member(c), plan(c))
        c.execute(text("UPDATE subscriptions SET payment_status = 'PAID' WHERE id = :i"), {"i": sid})
        c.execute(text("UPDATE subscriptions SET canceled_at = now() WHERE id = :i"), {"i": sid})


def test_subscription_delete_is_blocked(engine):
    with engine.begin() as c:
        sid = sub(c, member(c), plan(c))
    with pytest.raises(DBAPIError, match="interdit"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM subscriptions WHERE id = :i"), {"i": sid})


def test_payment_delete_is_blocked(engine):
    with engine.begin() as c:
        pid = payment(c, sub(c, member(c), plan(c)))
    with pytest.raises(DBAPIError, match="interdit"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM payments WHERE id = :i"), {"i": pid})


def test_payment_void_is_irreversible(engine):
    with engine.begin() as c:
        pid = payment(c, sub(c, member(c), plan(c)))
        c.execute(text("UPDATE payments SET status = 'VOIDED', voided_at = now() WHERE id = :i"), {"i": pid})
    with pytest.raises(DBAPIError, match="irreversible"):
        with engine.begin() as c:
            c.execute(text("UPDATE payments SET status = 'CONFIRMED', voided_at = NULL WHERE id = :i"), {"i": pid})


def test_payment_amount_must_be_positive_and_immutable(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            sid = sub(c, member(c), plan(c))
            method = c.execute(text("INSERT INTO payment_methods (code, name) VALUES (:c, 'M') RETURNING id"),
                               {"c": f"m-{_uid()}"}).scalar_one()
            c.execute(text("INSERT INTO payments (subscription_id, payment_method_id, amount, currency, paid_at) "
                           "VALUES (:s, :m, 0, 'EUR', now())"), {"s": sid, "m": method})
    with engine.begin() as c:
        pid = payment(c, sub(c, member(c), plan(c)))
    with pytest.raises(DBAPIError, match="immuables"):
        with engine.begin() as c:
            c.execute(text("UPDATE payments SET amount = 1 WHERE id = :i"), {"i": pid})


def test_eur_payment_requires_xof_settlement(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            sid = sub(c, member(c), plan(c))
            method = c.execute(text("INSERT INTO payment_methods (code, name) VALUES (:c, 'M') RETURNING id"),
                               {"c": f"m-{_uid()}"}).scalar_one()
            c.execute(text("INSERT INTO payments (subscription_id, payment_method_id, amount, currency, paid_at) "
                           "VALUES (:s, :m, 499, 'EUR', now())"), {"s": sid, "m": method})


def test_xof_payment_needs_no_settlement(engine):
    with engine.begin() as c:
        sid = sub(c, member(c), plan(c))
        method = c.execute(text("INSERT INTO payment_methods (code, name) VALUES (:c, 'M') RETURNING id"),
                           {"c": f"m-{_uid()}"}).scalar_one()
        c.execute(text("INSERT INTO payments (subscription_id, payment_method_id, amount, currency, paid_at) "
                       "VALUES (:s, :m, 3273, 'XOF', now())"), {"s": sid, "m": method})


def test_settlement_is_immutable(engine):
    with engine.begin() as c:
        pid = payment(c, sub(c, member(c), plan(c)))
    with pytest.raises(DBAPIError, match="immuables"):
        with engine.begin() as c:
            c.execute(text("UPDATE payments SET settled_amount = 1 WHERE id = :i"), {"i": pid})
