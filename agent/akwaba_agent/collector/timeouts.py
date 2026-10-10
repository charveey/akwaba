"""Timeouts conntrack lus sur la machine (jamais codés en dur) et âge d'un flux."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

DEFAULT_DIR = "/proc/sys/net/netfilter"

_TCP_FILES = {
    state: "nf_conntrack_tcp_timeout_" + state.lower()
    for state in ("SYN_SENT", "SYN_RECV", "ESTABLISHED", "FIN_WAIT", "CLOSE_WAIT", "LAST_ACK", "TIME_WAIT", "CLOSE")
}


def load_timeouts(base: str = DEFAULT_DIR) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for path in Path(base).glob("nf_conntrack_*timeout*"):
        try:
            out[path.name] = int(path.read_text().strip())
        except (OSError, ValueError):
            continue
    return out


def max_timeout(proto: str, state: Optional[str], assured: bool, timeouts: Dict[str, int]) -> Optional[int]:
    """Timeout initial d'un flux selon son protocole et son état. None si inconnu."""
    if proto == "tcp":
        name = _TCP_FILES.get(state or "")
        return timeouts.get(name) if name else None
    if proto == "udp":
        return timeouts.get("nf_conntrack_udp_timeout_stream" if assured else "nf_conntrack_udp_timeout")
    if proto == "icmp":
        return timeouts.get("nf_conntrack_icmp_timeout")
    if proto == "icmpv6":
        return timeouts.get("nf_conntrack_icmpv6_timeout")
    return timeouts.get("nf_conntrack_generic_timeout")
