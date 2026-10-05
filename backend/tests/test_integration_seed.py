"""Intégration Phase 4a : données de référence créées par la migration 0005."""
import os
import pathlib
import subprocess

import pytest
from sqlalchemy import create_engine, text

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


def test_plans_are_seeded(engine):
    with engine.connect() as c:
        rows = {r[0]: r[1:] for r in c.execute(text(
            "SELECT code, price_amount, currency, duration_months, is_unlimited FROM subscription_plans"))}
    assert rows["M1"] == (499, "EUR", 1, False)
    assert rows["M3"] == (1299, "EUR", 3, False)
    assert rows["M6"] == (2599, "EUR", 6, False)
    assert rows["M12"] == (5399, "EUR", 12, False)
    assert rows["PLATINUM"] == (0, "EUR", None, True)


def test_payment_methods_are_seeded(engine):
    with engine.connect() as c:
        codes = {r[0] for r in c.execute(text("SELECT code FROM payment_methods"))}
    assert {"CASH", "MOBILE_MONEY", "BANK_TRANSFER"} <= codes


def test_migration_is_reversible_one_step(engine):
    alembic("downgrade", "0004")
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM subscription_plans")).scalar_one()
    assert n == 0
    alembic("upgrade", "head")
