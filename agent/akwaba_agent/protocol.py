"""Signature HMAC des requêtes agent -> backend. Stdlib uniquement, Python 3.9."""
from __future__ import annotations

import hashlib
import hmac
import os
import stat
import time
from typing import Dict, Optional

VERSION = "v1"
MAX_BODY_BYTES = 1024 * 1024
ALLOWED_SKEW_SECONDS = 120

H_AGENT = "X-Akwaba-Agent"
H_TIMESTAMP = "X-Akwaba-Timestamp"
H_NONCE = "X-Akwaba-Nonce"
H_SIGNATURE = "X-Akwaba-Signature"


class SecretError(Exception):
    pass


def new_secret() -> str:
    return os.urandom(32).hex()


def new_nonce() -> str:
    return os.urandom(16).hex()


def load_secret(path: str) -> bytes:
    """Lit le secret (hexadécimal, 64 caractères). Refuse un fichier lisible par d'autres."""
    try:
        st = os.stat(path)
    except OSError:
        raise SecretError("fichier de secret introuvable")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise SecretError("permissions trop larges (0600 attendu)")
    try:
        with open(path, encoding="ascii") as fh:
            text = fh.read().strip()
        raw = bytes.fromhex(text)
    except (OSError, ValueError):
        raise SecretError("secret illisible")
    if len(raw) != 32:
        raise SecretError("secret de longueur invalide")
    return raw


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(method: str, path: str, timestamp: str, nonce: str, digest: str) -> bytes:
    return "\n".join((VERSION, method.upper(), path, timestamp, nonce, digest)).encode("utf-8")


def sign(secret: bytes, method: str, path: str, body: bytes, timestamp: str, nonce: str) -> str:
    msg = canonical(method, path, timestamp, nonce, body_digest(body))
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def build_headers(agent_id: str, secret: bytes, method: str, path: str, body: bytes,
                  now: Optional[float] = None, nonce: Optional[str] = None) -> Dict[str, str]:
    ts = str(int(time.time() if now is None else now))
    n = nonce or new_nonce()
    return {
        H_AGENT: agent_id,
        H_TIMESTAMP: ts,
        H_NONCE: n,
        H_SIGNATURE: sign(secret, method, path, body, ts, n),
    }


def verify(secret: bytes, method: str, path: str, body: bytes, timestamp: str, nonce: str,
           signature: str) -> bool:
    expected = sign(secret, method, path, body, timestamp, nonce)
    return hmac.compare_digest(expected, signature.lower())
