from app.core.security import (
    dummy_hash, hash_password, hash_token, new_token, tokens_equal, verify_password,
)


def test_hash_is_argon2id_and_verifies():
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct-horse-battery")


def test_wrong_password_is_rejected():
    assert not verify_password(hash_password("correct-horse-battery"), "wrong")


def test_garbage_hash_is_rejected_without_raising():
    assert verify_password("not-a-hash", "x") is False


def test_tokens_are_random_and_hash_is_sha256():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43
    assert len(hash_token(a)) == 64 and hash_token(a) != a


def test_dummy_hash_never_matches_and_tokens_equal():
    assert not verify_password(dummy_hash(), "anything")
    assert tokens_equal("abc", "abc") and not tokens_equal("abc", "abd")
