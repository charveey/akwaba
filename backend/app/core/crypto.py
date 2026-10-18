"""Chiffrement des champs sensibles (Fernet, clé FIELD_ENCRYPTION_KEY)."""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CryptoError(Exception):
    pass


@lru_cache
def _fernet() -> Fernet:
    return Fernet(get_settings().field_encryption_key.encode("ascii"))


def encrypt_field(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_field(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError):
        raise CryptoError("déchiffrement impossible")
