import os

import pytest

from akwaba_agent.protocol import (
    SecretError, build_headers, load_secret, new_nonce, new_secret, verify, H_NONCE, H_SIGNATURE, H_TIMESTAMP,
)

SECRET = bytes.fromhex("11" * 32)


def test_headers_roundtrip():
    h = build_headers("agent-1", SECRET, "post", "/api/agent/batches", b"{}", now=1_760_000_000.0)
    assert set(h) == {"X-Akwaba-Agent", H_TIMESTAMP, H_NONCE, H_SIGNATURE}
    assert verify(SECRET, "POST", "/api/agent/batches", b"{}", h[H_TIMESTAMP], h[H_NONCE], h[H_SIGNATURE])


def test_secret_must_be_private(tmp_path):
    p = tmp_path / "s"
    p.write_text(new_secret())
    p.chmod(0o644)
    with pytest.raises(SecretError):
        load_secret(str(p))
    p.chmod(0o600)
    assert len(load_secret(str(p))) == 32


def test_nonces_differ():
    assert new_nonce() != new_nonce()
