"""Agrégation par IP source cliente. Données agrégées uniquement."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Dict, Iterable, List

from akwaba_agent.collector.conntrack import Flow
from akwaba_agent.collector.timeouts import max_timeout


@dataclass(frozen=True)
class SourceSummary:
    src: str
    total_flows: int
    recent_flows: int
    recent_replied_flows: int
    unknown_age_flows: int


def flow_age(flow: Flow, timeouts: Dict[str, int]):
    """Secondes depuis la dernière activité, ou None si le timeout maximal est inconnu ou incohérent."""
    mx = max_timeout(flow.proto, flow.state, flow.assured, timeouts)
    if mx is None or flow.timeout_remaining > mx:
        return None
    return mx - flow.timeout_remaining


def summarize(
    flows: Iterable[Flow], timeouts: Dict[str, int], window_seconds: int = 60,
    exclude_sources: Iterable[str] = (),
) -> List[SourceSummary]:
    if window_seconds <= 0:
        raise ValueError("window_seconds doit être > 0")
    excluded = {str(ipaddress.ip_address(x)) for x in exclude_sources}
    counters: Dict[str, List[int]] = {}
    for f in flows:
        if not f.src_is_client or not f.egress or f.src in excluded:
            continue
        c = counters.setdefault(f.src, [0, 0, 0, 0])  # total, récents, récents+réponse, âge inconnu
        c[0] += 1
        age = flow_age(f, timeouts)
        if age is None:
            c[3] += 1
        elif age <= window_seconds:
            c[1] += 1
            if f.replied:
                c[2] += 1
    out = [SourceSummary(src, *c) for src, c in counters.items()]
    return sorted(out, key=lambda s: (-s.recent_replied_flows, s.src))
