"""Un cycle d'échantillonnage : conntrack -> agrégation -> whois/status -> lot. Aucun envoi réseau."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Set

from akwaba_agent.aggregator.activity import ActivityTracker
from akwaba_agent.aggregator.flows import summarize
from akwaba_agent.collector.conntrack import CollectorError, Flow, ParseResult

SCHEMA_VERSION = 1


@dataclass
class Health:
    conntrack_ok: bool = False
    status_ok: bool = False
    whois_ok: bool = True
    lines: int = 0
    unparsed: int = 0
    timeouts_loaded: int = 0
    warnings: List[str] = field(default_factory=list)
    conntrack_error: Optional[str] = None   # catégorie seulement, jamais de texte


@dataclass
class SampleResult:
    health: Health
    payload: Optional[dict]
    observations: int


class Sampler:
    def __init__(
        self,
        read: Callable[[], ParseResult],
        timeouts: Callable[[], Dict[str, int]],
        status: Callable[[], object],
        resolver: object,
        tracker: Optional[ActivityTracker] = None,
        window_seconds: int = 60,
        exclude_sources: Iterable[str] = (),
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds doit être > 0")
        self._read, self._timeouts, self._status, self._resolver = read, timeouts, status, resolver
        self._tracker = tracker or ActivityTracker()
        self._window = window_seconds
        self._excluded = tuple(str(ipaddress.ip_address(x)) for x in exclude_sources)
        self._prev_keys: Optional[Dict[str, Set[str]]] = None

    def _reset(self) -> None:
        self._tracker.reset()
        self._prev_keys = None

    def _flow_keys(self, flows: Iterable[Flow]) -> Dict[str, Set[str]]:
        keys: Dict[str, Set[str]] = {}
        for f in flows:
            if f.src_is_client and f.egress and f.src not in self._excluded:
                keys.setdefault(f.src, set()).add(f.key)
        return keys

    def sample(self, now: datetime) -> SampleResult:
        health = Health()
        try:
            parsed = self._read()
        except CollectorError as exc:
            self._reset()                       # aucune observation, aucune série, aucun « nouveau »
            health.warnings.append("conntrack : %s" % exc)
            health.conntrack_error = exc.category
            return SampleResult(health, None, 0)
        health.conntrack_ok = True
        health.lines, health.unparsed = parsed.lines, parsed.unparsed
        health.warnings.extend(parsed.warnings)

        timeouts = self._timeouts()
        health.timeouts_loaded = len(timeouts)
        if not timeouts:
            health.warnings.append("timeouts conntrack introuvables : âges inconnus")

        summaries = sorted(
            summarize(parsed.flows, timeouts, self._window, self._excluded), key=lambda s: s.src)
        current = self._flow_keys(parsed.flows)
        previous, self._prev_keys = self._prev_keys, current
        streaks = self._tracker.update({s.src: s.recent_replied_flows for s in summaries})

        status = self._status()
        health.status_ok = bool(getattr(status, "ok", False))

        observations = []
        for s in summaries:
            res = self._resolver.resolve(s.src)
            identity = None
            if res.identity is not None:
                i = res.identity
                identity = {"login_name": i.login_name, "tailscale_user_id": i.tailscale_user_id,
                            "stable_id": i.stable_id, "is_sharee": i.is_sharee}
            peer = None
            if health.status_ok:
                p = status.by_ip.get(s.src)
                peer = {"known": p is not None, "online": p.online if p else None,
                        "expired": p.expired if p else None, "tagged": p.tagged if p else None}
            streak = streaks.get(s.src, 0)
            observations.append({
                "src_ip": s.src,
                "total_flows": s.total_flows,
                "recent_flows": s.recent_flows,
                "recent_replied_flows": s.recent_replied_flows,
                "unknown_age_flows": s.unknown_age_flows,
                "new_flows": None if previous is None else len(current.get(s.src, set()) - previous.get(s.src, set())),
                "active_window": streak > 0,
                "consecutive_active_windows": streak,
                "active_confirmed": streak >= self._tracker.required_windows,
                "identity_status": res.status,
                "identity_technical_error": res.technical_error,
                "identity": identity,
                "peer": peer,
            })
        health.whois_ok = bool(self._resolver.whois_ok)

        payload = {
            "schema": SCHEMA_VERSION,
            "sampled_at": now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "window_seconds": self._window,
            "conntrack_ok": True,
            "status_ok": health.status_ok,
            "whois_ok": health.whois_ok,
            "observations": observations,
        }
        return SampleResult(health, payload, len(observations))
