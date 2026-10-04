import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID = {
    "database_url": "postgresql+psycopg://u:p@db:5432/x",
    "secret_key": "s" * 48,
    "field_encryption_key": "A" * 43 + "=",
}


def make(**overrides) -> Settings:
    return Settings(**{**VALID, **overrides})


def test_defaults_are_dry_run():
    s = make()
    assert s.revocation_mode == "dry_run"
    assert s.enforcer_enabled is False
    assert s.unknown_user_grace_period_hours == 24
    assert s.default_grace_period_days == 3
    assert s.renewal_in_grace_start == "PREVIOUS_EXPIRY"
    assert s.default_currency == "XOF"


def test_enforce_mode_is_rejected():
    with pytest.raises(ValidationError):
        make(revocation_mode="enforce")


def test_enforcer_enabled_is_rejected():
    with pytest.raises(ValidationError):
        make(enforcer_enabled=True)


def test_short_secret_key_is_rejected():
    with pytest.raises(ValidationError):
        make(secret_key="short")


def test_invalid_secret_is_not_echoed_in_error():
    with pytest.raises(ValidationError) as exc:
        make(secret_key="short-secret-value")
    assert "short-secret-value" not in str(exc.value)


def test_standard_base64_encryption_key_is_rejected():
    with pytest.raises(ValidationError):
        make(field_encryption_key="A" * 42 + "+=")


def test_wrong_database_scheme_is_rejected():
    with pytest.raises(ValidationError):
        make(database_url="sqlite:///x.db")


def test_agent_allowed_ips_are_parsed():
    s = make(agent_allowed_ips="100.85.214.5, 100.64.0.0/10")
    assert s.agent_allowed_ip_list == ["100.85.214.5", "100.64.0.0/10"]


def test_invalid_agent_ip_is_rejected():
    with pytest.raises(ValidationError):
        make(agent_allowed_ips="not-an-ip")
