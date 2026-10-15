"""Lecture de `tailscale status --json`. Aucune donnée personnelle conservée (ni login, ni hostname)."""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

CACHE_TTL = 15


@dataclass(frozen=True)
class Peer:
    stable_id: Optional[str]
    ips: List[str]
    online: bool
    active: bool
    last_handshake: Optional[datetime]
    last_seen: Optional[datetime]
    last_write: Optional[datetime]
    rx_bytes: Optional[int]
    tx_bytes: Optional[int]
    is_sharee: bool
    expired: bool
    exit_node: bool
    tagged: bool


@dataclass
class StatusResult:
    ok: bool
    peers: List[Peer] = field(default_factory=list)
    by_ip: Dict[str, Peer] = field(default_factory=dict)
    duplicate_ips: int = 0
    error: Optional[str] = None


def parse_time(value) -> Optional[datetime]:
    """Format Go RFC 3339. 'Jamais' (année 1) ou illisible : None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python 3.9 : fromisoformat n'accepte que 0, 3 ou 6 décimales.
    if "." in text:
        head, _, rest = text.partition(".")
        digits = ""
        for ch in rest:
            if ch.isdigit():
                digits += ch
            else:
                break
        tail = rest[len(digits):]
        text = head + "." + (digits + "000000")[:6] + tail
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.year <= 1:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _bool(value) -> bool:
    return value is True


def _int(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_peer(raw) -> Optional[Peer]:
    if not isinstance(raw, dict):
        return None
    ips = raw.get("TailscaleIPs")
    if not isinstance(ips, list):
        ips = []
    ips = [ip for ip in ips if isinstance(ip, str)]
    stable = raw.get("ID")
    tags = raw.get("Tags")
    return Peer(
        stable_id=stable if isinstance(stable, str) and stable else None,
        ips=ips,
        online=_bool(raw.get("Online")),
        active=_bool(raw.get("Active")),
        last_handshake=parse_time(raw.get("LastHandshake")),
        last_seen=parse_time(raw.get("LastSeen")),
        last_write=parse_time(raw.get("LastWrite")),
        rx_bytes=_int(raw.get("RxBytes")),
        tx_bytes=_int(raw.get("TxBytes")),
        is_sharee=_bool(raw.get("ShareeNode")),
        expired=_bool(raw.get("Expired")),
        exit_node=_bool(raw.get("ExitNode")),
        tagged=isinstance(tags, list) and len(tags) > 0,
    )


def parse_status(text: str) -> StatusResult:
    try:
        data = json.loads(text)
    except ValueError:
        return StatusResult(ok=False, error="JSON invalide")
    if not isinstance(data, dict):
        return StatusResult(ok=False, error="structure inattendue")
    raw_peers = data.get("Peer")
    if raw_peers is None:
        raw_peers = {}          # tailnet sans pair : valide
    if not isinstance(raw_peers, dict):
        return StatusResult(ok=False, error="Peer inattendu")
    result = StatusResult(ok=True)
    for raw in raw_peers.values():
        peer = _parse_peer(raw)
        if peer is None:
            continue
        result.peers.append(peer)
        for ip in peer.ips:
            if ip in result.by_ip:
                result.duplicate_ips += 1
            else:
                result.by_ip[ip] = peer
    return result


class StatusReader:
    def __init__(
        self,
        prefix: Sequence[str] = (),
        run: Callable = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        timeout: int = 15,
    ) -> None:
        self._prefix, self._run, self._clock, self._timeout = list(prefix), run, clock, timeout
        self._cached: Optional[StatusResult] = None
        self._expires = 0.0

    def read(self) -> StatusResult:
        now = self._clock()
        if self._cached is not None and now < self._expires:
            return self._cached
        result = self._execute()
        self._cached = result
        self._expires = now + CACHE_TTL
        return result

    def _execute(self) -> StatusResult:
        cmd = self._prefix + ["tailscale", "status", "--json"]
        try:
            proc = self._run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=self._timeout)
        except FileNotFoundError:
            return StatusResult(ok=False, error="tailscale introuvable")
        except subprocess.TimeoutExpired:
            return StatusResult(ok=False, error="délai dépassé")
        except OSError:
            return StatusResult(ok=False, error="exécution impossible")
        if proc.returncode != 0:
            return StatusResult(ok=False, error="code %d" % proc.returncode)
        return parse_status(proc.stdout)
