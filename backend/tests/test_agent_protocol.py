import pytest

from app.core.agent_protocol import (
    ALLOWED_SKEW_SECONDS, NonceCache, SecretError, build_headers, check_timestamp, load_secret,
    new_nonce, new_secret, sign, verify, H_NONCE, H_SIGNATURE, H_TIMESTAMP,
)

SECRET = bytes.fromhex("11" * 32)
BODY = b'{"a":1}'
NOW = 1_760_000_000.0


def hdr(**kw):
    return build_headers("agent-1", kw.pop("secret", SECRET), "POST", "/api/agent/batches",
                         kw.pop("body", BODY), now=kw.pop("now", NOW), nonce=kw.pop("nonce", "ab" * 16))


def ok(h, body=BODY, secret=SECRET, method="POST", path="/api/agent/batches"):
    return verify(secret, method, path, body, h[H_TIMESTAMP], h[H_NONCE], h[H_SIGNATURE])


def test_valid_signature_verifies():
    assert ok(hdr())


def test_any_change_breaks_the_signature():
    h = hdr()
    assert not ok(h, body=b'{"a":2}')
    assert not ok(h, secret=bytes.fromhex("22" * 32))
    assert not ok(h, method="PUT")
    assert not ok(h, path="/api/agent/other")
    forged = dict(h, **{H_NONCE: "cd" * 16})
    assert not ok(forged)
    forged = dict(h, **{H_TIMESTAMP: str(int(NOW) + 1)})
    assert not ok(forged)


def test_signature_is_case_insensitive_hex_and_garbage_is_rejected():
    h = hdr()
    assert verify(SECRET, "POST", "/api/agent/batches", BODY, h[H_TIMESTAMP], h[H_NONCE], h[H_SIGNATURE].upper())
    assert not verify(SECRET, "POST", "/api/agent/batches", BODY, h[H_TIMESTAMP], h[H_NONCE], "zz")
    assert not verify(SECRET, "POST", "/api/agent/batches", BODY, h[H_TIMESTAMP], h[H_NONCE], "")


def test_nonce_and_secret_generation():
    assert len(new_nonce()) == 32 and new_nonce() != new_nonce()
    s = new_secret()
    assert len(s) == 64 and bytes.fromhex(s)


def test_timestamp_window():
    assert check_timestamp(str(int(NOW)), NOW) == "ok"
    assert check_timestamp(str(int(NOW) - ALLOWED_SKEW_SECONDS), NOW) == "ok"
    assert check_timestamp(str(int(NOW) + ALLOWED_SKEW_SECONDS), NOW) == "ok"
    assert check_timestamp(str(int(NOW) - ALLOWED_SKEW_SECONDS - 1), NOW) == "clock_skew"
    assert check_timestamp(str(int(NOW) + ALLOWED_SKEW_SECONDS + 1), NOW) == "clock_skew"
    assert check_timestamp("hier", NOW) == "malformed" and check_timestamp("", NOW) == "malformed"


def test_nonce_replay_is_refused_then_allowed_after_expiry():
    c = NonceCache(ttl_seconds=100)
    assert c.check_and_store("a", "n1", 0.0)
    assert not c.check_and_store("a", "n1", 50.0)
    assert c.check_and_store("b", "n1", 50.0)          # un autre agent a son propre espace
    assert c.check_and_store("a", "n1", 101.0)         # expiré


def test_nonce_cache_is_bounded():
    c = NonceCache(ttl_seconds=1000, max_entries=5)
    for i in range(20):
        assert c.check_and_store("a", "n%d" % i, float(i))
    assert len(c._seen) <= 5


def test_secret_file_rules(tmp_path):
    p = tmp_path / "secret"
    p.write_text("11" * 32)
    p.chmod(0o600)
    assert load_secret(str(p)) == SECRET
    p.chmod(0o640)
    with pytest.raises(SecretError):
        load_secret(str(p))
    p.chmod(0o600)
    p.write_text("pas-hex")
    with pytest.raises(SecretError):
        load_secret(str(p))
    p.write_text("11" * 16)
    with pytest.raises(SecretError):
        load_secret(str(p))
    with pytest.raises(SecretError):
        load_secret(str(tmp_path / "absent"))


def test_agent_and_backend_modules_are_identical():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    agent = (root / "agent/akwaba_agent/protocol.py").read_text()
    backend = (root / "backend/app/core/agent_protocol.py").read_text()
    assert backend.startswith(agent)
