"""Lecture de `conntrack -L` : tuple ORIGINAL uniquement.

Aucune IP ni port de destination n'est conservé : l'IP de destination sert seulement à décider
si le flux sort du réseau, puis elle est jetée.
"""
from __future__ import annotations

import ipaddress
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

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
    src = dst = state = None
    flags = set()
    for tok in tokens[3:]:
        if tok.startswith("["):
            flags.add(tok.strip("[]"))
        elif "=" in tok:
            key, _, value = tok.partition("=")
            # Première occurrence = tuple original. Les ports ne sont même pas lus.
            if key == "src" and src is None:
                src = value
            elif key == "dst" and dst is None:
                dst = value
        elif state is None and src is None:
            state = tok
    if src is None or dst is None:
        return None
    try:
        src_ip = ipaddress.ip_address(src)
        dst_ip = ipaddress.ip_address(dst)
    except ValueError:
        return None
    return Flow(
        proto=tokens[0].lower(),
        state=state.upper() if state else None,
        timeout_remaining=timeout,
        src=str(src_ip),
        src_is_client=is_client_source(src_ip),
        egress=is_external_destination(dst_ip),
        replied="UNREPLIED" not in flags,
        assured="ASSURED" in flags,
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
        raise CollectorError("format inattendu : aucune ligne conntrack reconnue")
    return ParseResult(flows=flows, lines=lines, unparsed=unparsed)


def _run(cmd: Sequence[str], run: Callable, timeout: int) -> str:
    try:
        proc = run(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   universal_newlines=True, timeout=timeout)
    except FileNotFoundError:
        raise CollectorError("commande introuvable : %s" % cmd[0])
    except subprocess.TimeoutExpired:
        raise CollectorError("délai dépassé (%d s)" % timeout)
    except OSError as exc:
        raise CollectorError("exécution impossible : %s" % exc)
    if proc.returncode != 0:
        raise CollectorError("conntrack a échoué (code %d) : %s" % (proc.returncode, (proc.stderr or "")[:200].strip()))
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
