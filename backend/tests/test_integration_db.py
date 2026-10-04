"""Tests d'intégration : migrations + contraintes sur un PostgreSQL réel."""
import os
import pathlib
import subprocess

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


def _new_admin(conn, email="admin@example.test"):
    return conn.execute(
        text("INSERT INTO admin_users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
        {"e": email},
    ).scalar_one()


def test_expected_tables_exist(engine):
    with engine.connect() as c:
        names = {r[0] for r in c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
    assert {"admin_users", "admin_sessions", "audit_logs", "settings", "job_runs"} <= names


def test_models_and_migration_are_in_sync(engine):
    alembic("check")


def test_audit_insert_is_allowed(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO audit_logs (action, actor_label) VALUES ('test.insert', 'system')"))


def test_audit_update_is_blocked(engine):
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as c:
            c.execute(text("INSERT INTO audit_logs (action) VALUES ('test.update')"))
            c.execute(text("UPDATE audit_logs SET action = 'x'"))


def test_audit_delete_is_blocked(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO audit_logs (action) VALUES ('test.delete')"))
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM audit_logs"))


def test_audit_truncate_is_blocked(engine):
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as c:
            c.execute(text("TRUNCATE audit_logs"))


def test_admin_email_must_be_lowercase(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            _new_admin(c, "Admin@Example.test")


def test_admin_email_is_unique(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            _new_admin(c, "dup@example.test")
            _new_admin(c, "dup@example.test")


def test_deleting_admin_deletes_sessions(engine):
    with engine.begin() as c:
        admin_id = _new_admin(c, "sess@example.test")
        c.execute(text(
            "INSERT INTO admin_sessions (admin_user_id, token_hash, csrf_token, expires_at) "
            "VALUES (:a, :h, 'c', now() + interval '1 hour')"), {"a": admin_id, "h": "h" * 64})
    with engine.begin() as c:
        c.execute(text("DELETE FROM admin_users WHERE id = :a"), {"a": admin_id})
        n = c.execute(text("SELECT count(*) FROM admin_sessions WHERE admin_user_id = :a"),
                      {"a": admin_id}).scalar_one()
    assert n == 0
