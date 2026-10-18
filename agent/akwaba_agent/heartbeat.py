"""Heartbeat : santé de l'agent. Aucune IP, aucun login, aucun texte d'erreur."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from akwaba_agent.collector.timeouts import DEFAULT_DIR

SCHEMA_VERSION = 1


def read_conntrack_load(base: str = DEFAULT_DIR) -> Optional[Tuple[int, int]]:
    """(entrées actuelles, maximum) de la table conntrack, ou None si illisible."""
    try:
        with open(os.path.join(base, "nf_conntrack_count")) as fh:
            count = int(fh.read().strip())
        with open(os.path.join(base, "nf_conntrack_max")) as fh:
            maximum = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    if maximum <= 0 or count < 0:
        return None
    return count, maximum


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_heartbeat(now: float, res, stats, spool, load: Optional[Tuple[int, int]],
                    saturation_threshold: float, version: str) -> Dict:
    health = res.health if res is not None else None
    entries = maximum = saturation = None
    saturated = False
    if load is not None:
        entries, maximum = load
        saturation = round(entries / maximum, 3)
        saturated = saturation >= saturation_threshold
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "agent_version": version,
        "cycle_failed": res is None,
        "conntrack_ok": bool(health.conntrack_ok) if health else False,
        "conntrack_error": health.conntrack_error if health else None,
        "status_ok": bool(health.status_ok) if health else False,
        "whois_ok": bool(health.whois_ok) if health else False,
        "lines": health.lines if health else 0,
        "unparsed": health.unparsed if health else 0,
        "timeouts_loaded": health.timeouts_loaded if health else 0,
        "observations": res.observations if res is not None else 0,
        "conntrack_entries": entries,
        "conntrack_max": maximum,
        "conntrack_saturation": saturation,
        "conntrack_saturated": saturated,
        "spool_pending": spool.pending() if spool is not None else None,
        "spool_oldest_age_seconds": spool.oldest_age(now) if spool is not None else None,
        "dropped_batches": spool.dropped_batches if spool is not None else 0,
        "spool_recovered_corruption": bool(spool.recovered_corruption) if spool is not None else False,
        "cycles": stats.cycles,
        "loop_errors": stats.errors,
        "last_error": stats.last_error,
        "last_cycle_at": _iso(stats.last_cycle_at),
    }


def write_atomic(path: str, data: Dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, separators=(",", ":"), sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class TransitionLogger:
    """Ne produit une ligne que lorsque l'état change (journal minimal)."""

    KEYS = ("conntrack_ok", "status_ok", "whois_ok", "conntrack_saturated")

    def __init__(self) -> None:
        self._last: Optional[Dict] = None
        self._dropped = 0

    def changes(self, hb: Dict) -> List[str]:
        cur = {k: hb[k] for k in self.KEYS}
        lines: List[str] = []
        if self._last is None:
            lines.append("etat initial : " + " ".join("%s=%s" % (k, cur[k]) for k in self.KEYS))
        elif cur != self._last:
            lines.append("changement : " + " ".join(
                "%s=%s" % (k, cur[k]) for k in self.KEYS if cur[k] != self._last[k]))
        if hb["dropped_batches"] > self._dropped:
            lines.append("lots supprimes (spool plein) : total=%d" % hb["dropped_batches"])
        self._last, self._dropped = cur, hb["dropped_batches"]
        return lines
