"""Lecture de `conntrack -L` : tuple ORIGINAL uniquement.

Aucune IP ni port de destination n'est conservé : l'IP de destination sert seulement à décider
si le flux sort du réseau, puis elle est jetée.
"""
from __future__ import annotations

import ipaddress
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence
import hashlib
import os

_KEY_SALT = os.urandom(16)  # propre au processus, jamais écrit sur disque


def _flow_key(proto: str, src: str, dst: str, sport, dport, ident) -> str:
    """Empreinte opaque (8 octets, salée) : sert uniquement à détecter un flux déjà vu."""
    raw = "|".join((proto, src, dst, sport or "", dport or "", ident or "")).encode("ascii", "ignore")
    return hashlib.blake2b(raw, key=_KEY_SALT, digest_size=8).hexdigest()


TAILSCALE_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)

# Liste explicite (et non is_global) : les plages de documentation 203.0.113.0/24 etc. restent « extérieures ».
_NOT_EXTERNAL = TAILSCALE_NETS + tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16",
    "224.0.0.0/4", "240.0.0.0/4", "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8",
))


class CollectorError(Exception):
    """conntrack illisible : l'appelant doit signaler conntrack_ok=false, sans observation."""

    def __init__(self, message: str, category: str = "other") -> None:
        super().__init__(message)
        self.category = category


def _in(ip, nets) -> bool:
    return any(ip in net for net in nets)


def is_client_source(ip) -> bool:
    return _in(ip, TAILSCALE_NETS)


def is_external_destination(ip) -> bool:
    return not _in(ip, _NOT_EXTERNAL)


@dataclass(frozen=True)
class Flow:
    proto: str
    state: Optional[str]
    timeout_remaining: int
    src: str            # IP source normalisée
    src_is_client: bool  # source dans les plages Tailscale
    egress: bool         # la destination est hors Tailscale et hors réseaux locaux
    replied: bool
    assured: bool
    key: str            # empreinte opaque du flux (aucune destination ni port récupérable)


@dataclass
class ParseResult:
    flows: List[Flow]
    lines: int
    unparsed: int
    warnings: List[str] = field(default_factory=list)


def parse_line(line: str) -> Optional[Flow]:
    tokens = line.split()
    if tokens and tokens[0] in ("ipv4", "ipv6"):  # format -o extended
        tokens = tokens[2:]
    if len(tokens) < 4:
        return None
    try:
        timeout = int(tokens[2])
    except ValueError:
        return None
    if timeout < 0:
        return None
    src = dst = state = sport = dport = ident = None
    flags = set()
    for tok in tokens[3:]:
        if tok.startswith("["):
            flags.add(tok.strip("[]"))
        elif "=" in tok:
            name, _, value = tok.partition("=")
            # Première occurrence = tuple original. Destination et ports ne servent qu'à l'empreinte.
            if name == "src" and src is None:
                src = value
            elif name == "dst" and dst is None:
                dst = value
            elif name == "sport" and sport is None:
                sport = value
            elif name == "dport" and dport is None:
                dport = value
            elif name == "id" and ident is None:
                ident = value
        elif state is None and src is None:
            state = tok
    if src is None or dst is None:
        return None
    try:
        src_ip = ipaddress.ip_address(src)
        dst_ip = ipaddress.ip_address(dst)
    except ValueError:
        return None
    proto = tokens[0].lower()
    return Flow(
        proto=proto,
        state=state.upper() if state else None,
        timeout_remaining=timeout,
        src=str(src_ip),
        src_is_client=is_client_source(src_ip),
        egress=is_external_destination(dst_ip),
        replied="UNREPLIED" not in flags,
        assured="ASSURED" in flags,
        key=_flow_key(proto, str(src_ip), str(dst_ip), sport, dport, ident),
    )

def parse_output(text: str) -> ParseResult:
    flows: List[Flow] = []
    lines = unparsed = 0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        lines += 1
        flow = parse_line(raw)
        if flow is None:
            unparsed += 1
        else:
            flows.append(flow)
    if lines and not flows:
        raise CollectorError("format inattendu : aucune ligne conntrack reconnue", "format")
    return ParseResult(flows=flows, lines=lines, unparsed=unparsed)


def _run(cmd: Sequence[str], run: Callable, timeout: int) -> str:
    try:
        proc = run(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   universal_newlines=True, timeout=timeout)
    except FileNotFoundError:
        raise CollectorError("commande introuvable : %s" % cmd[0], "not_found")
    except subprocess.TimeoutExpired:
        raise CollectorError("délai dépassé (%d s)" % timeout, "timeout")
    except OSError as exc:
        raise CollectorError("exécution impossible : %s" % exc, "exec")
    if proc.returncode != 0:
        err = (proc.stderr or "")[:200].strip()
        low = err.lower()
        if "effective uid is not 0" in low or "password is required" in low or "not allowed" in low:
            category = "sudo_denied"
        else:
            category = "exit_code"
        raise CollectorError("conntrack a échoué (code %d) : %s" % (proc.returncode, err), category)
    return proc.stdout


def read_conntrack(prefix: Sequence[str] = (), run: Callable = subprocess.run, timeout: int = 10) -> ParseResult:
    base = list(prefix) + ["conntrack", "-L", "-f"]
    v4 = parse_output(_run(base + ["ipv4"], run, timeout))
    warnings: List[str] = []
    flows, lines, unparsed = list(v4.flows), v4.lines, v4.unparsed
    try:
        v6 = parse_output(_run(base + ["ipv6"], run, timeout))
        flows += v6.flows
        lines += v6.lines
        unparsed += v6.unparsed
    except CollectorError as exc:
        warnings.append("ipv6 non lu : %s" % exc)
    return ParseResult(flows=flows, lines=lines, unparsed=unparsed, warnings=warnings)
