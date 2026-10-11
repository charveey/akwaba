"""Résolution IP Tailscale -> identité via la LocalAPI. Un échec est UNRESOLVED, jamais une identité inconnue."""
from __future__ import annotations

import http.client
import json
import socket
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

SOCKET_PATH = "/var/run/tailscale/tailscaled.sock"
RESOLVED_TTL = 300
UNRESOLVED_TTL = 30


@dataclass(frozen=True)
class Identity:
    login_name: str
    display_name: Optional[str]
    tailscale_user_id: Optional[int]
    stable_id: Optional[str]
    hostname: Optional[str]
    is_sharee: bool


@dataclass(frozen=True)
class WhoisResult:
    status: str                      # "RESOLVED" | "UNRESOLVED"
    identity: Optional[Identity] = None
    technical_error: bool = False    # True : LocalAPI injoignable ou réponse illisible


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("local-tailscaled.sock", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def fetch_whois(ip: str, socket_path: str = SOCKET_PATH, timeout: float = 3.0) -> Tuple[int, bytes]:
    conn = _UnixHTTPConnection(socket_path, timeout)
    try:
        conn.request("GET", "/localapi/v0/whois?addr=" + ip, headers={"Host": "local-tailscaled.sock"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _as_dict(value):
    return value if isinstance(value, dict) else None


def parse_whois(body: bytes) -> WhoisResult:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return WhoisResult("UNRESOLVED", technical_error=True)
    data = _as_dict(data)
    if data is None:
        return WhoisResult("UNRESOLVED", technical_error=True)
    node, profile = data.get("Node"), data.get("UserProfile")
    if (node is not None and _as_dict(node) is None) or (profile is not None and _as_dict(profile) is None):
        return WhoisResult("UNRESOLVED", technical_error=True)   # structure inattendue
    node, profile = node or {}, profile or {}
    login = profile.get("LoginName")
    if not isinstance(login, str) or not login.strip():
        return WhoisResult("UNRESOLVED")
    hostinfo = node.get("Hostinfo")
    if hostinfo is not None and _as_dict(hostinfo) is None:
        return WhoisResult("UNRESOLVED", technical_error=True)
    hostinfo = hostinfo or {}
    sharee = hostinfo.get("ShareeNode", False)
    if not isinstance(sharee, bool):                              # valeur malformée : jamais un « client » par défaut
        return WhoisResult("UNRESOLVED", technical_error=True)
    uid = profile.get("ID")
    return WhoisResult("RESOLVED", Identity(
        login_name=login.strip().lower(),
        display_name=profile.get("DisplayName") or None,
        tailscale_user_id=uid if isinstance(uid, int) and not isinstance(uid, bool) else None,
        stable_id=node.get("StableID") or None,
        hostname=hostinfo.get("Hostname") or None,
        is_sharee=sharee,
    ))


class WhoisResolver:
    def __init__(
        self,
        fetch: Callable[[str], Tuple[int, bytes]] = fetch_whois,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch, self._clock = fetch, clock
        self._cache: Dict[str, Tuple[float, WhoisResult]] = {}
        self.last_technical_error = False

    def resolve(self, ip: str) -> WhoisResult:
        now = self._clock()
        hit = self._cache.get(ip)
        if hit and hit[0] > now:
            return hit[1]
        result = self._lookup(ip)
        self.last_technical_error = result.technical_error
        ttl = RESOLVED_TTL if result.status == "RESOLVED" else UNRESOLVED_TTL
        self._cache[ip] = (now + ttl, result)
        return result

    def _lookup(self, ip: str) -> WhoisResult:
        try:
            status, body = self._fetch(ip)
        except Exception:  # noqa: BLE001 : toute panne de socket devient UNRESOLVED
            return WhoisResult("UNRESOLVED", technical_error=True)
        if status == 200:
            return parse_whois(body)
        if status in (404, 400):          # nœud inconnu de la LocalAPI : ce n'est pas une panne
            return WhoisResult("UNRESOLVED")
        return WhoisResult("UNRESOLVED", technical_error=True)

    @property
    def whois_ok(self) -> bool:
        return not self.last_technical_error
