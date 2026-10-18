import pytest

from app.core.agent_protocol import H_AGENT, H_NONCE, H_SIGNATURE, H_TIMESTAMP
from app.core.crypto import CryptoError, decrypt_field, encrypt_field
from app.services.agent_auth import AgentAuthError, ip_allowed, parse_headers

GOOD = {H_AGENT: "agt_0123456789abcdef", H_TIMESTAMP: "1760000000", H_NONCE: "ab" * 16, H_SIGNATURE: "cd" * 32}


@pytest.mark.parametrize("ip, allowed, expected", [
    ("100.85.214.5", ["100.85.214.5"], True),
    ("100.85.214.5", ["100.64.0.0/10"], True),
    ("100.85.214.6", ["100.85.214.5"], False),
    ("::ffff:100.85.214.5", ["100.85.214.5"], True),
    ("testclient", ["100.85.214.5"], False),
    ("garbage", ["0.0.0.0/0"], False),
    (None, ["100.85.214.5"], False),
    ("100.85.214.5", [], False),
    ("100.85.214.5", ["pas-un-reseau", "100.85.214.5"], True),
])
def test_ip_allowlist(ip, allowed, expected):
    assert ip_allowed(ip, allowed) is expected


def test_parse_headers_accepts_a_good_set():
    assert parse_headers(GOOD) == ("agt_0123456789abcdef", "1760000000", "ab" * 16, "cd" * 32)


@pytest.mark.parametrize("name", [H_AGENT, H_TIMESTAMP, H_NONCE, H_SIGNATURE])
def test_missing_header(name):
    h = {k: v for k, v in GOOD.items() if k != name}
    with pytest.raises(AgentAuthError) as e:
        parse_headers(h)
    assert e.value.reason == "missing_header"


@pytest.mark.parametrize("name, value", [
    (H_AGENT, "agt bad"), (H_AGENT, "x" * 65), (H_AGENT, "a\nb"),
    (H_TIMESTAMP, "-5"), (H_TIMESTAMP, "12.5"), (H_TIMESTAMP, "abc"),
    (H_NONCE, "XYZ"), (H_NONCE, "ab"), (H_SIGNATURE, "zz" * 32), (H_SIGNATURE, "cd" * 31),
])
def test_malformed_header(name, value):
    with pytest.raises(AgentAuthError) as e:
        parse_headers({**GOOD, name: value})
    assert e.value.reason == "malformed_header"


def test_field_encryption_roundtrip_and_randomization():
    token = encrypt_field("secret-value")
    assert token != "secret-value" and decrypt_field(token) == "secret-value"
    assert encrypt_field("secret-value") != token            # IV aléatoire


def test_invalid_ciphertext_is_refused():
    with pytest.raises(CryptoError):
        decrypt_field("pas-un-jeton-fernet")
