"""Boucle d'échantillonnage : un cycle par intervalle, jamais interrompue par une exception."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass
class LoopStats:
    cycles: int = 0
    enqueued: int = 0
    errors: int = 0
    last_error: Optional[str] = None     # nom de classe seulement, jamais le message


def run_loop(
    sampler,
    spool,
    interval: float,
    wall: Callable[[], float] = time.time,
    mono: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], object] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
    max_cycles: Optional[int] = None,
    on_cycle: Optional[Callable] = None,
) -> LoopStats:
    stats = LoopStats()
    while not should_stop():
        started, now_wall = mono(), wall()
        try:
            res = sampler.sample(datetime.fromtimestamp(now_wall, timezone.utc))
            if spool is not None and res.payload is not None and res.observations > 0:
                spool.enqueue(json.dumps(res.payload, separators=(",", ":"), sort_keys=True), now_wall)
                stats.enqueued += 1
            if on_cycle is not None:
                on_cycle(res, spool)
        except Exception as exc:  # noqa: BLE001 : la boucle ne doit jamais mourir
            stats.errors += 1
            stats.last_error = type(exc).__name__
        stats.cycles += 1
        if max_cycles is not None and stats.cycles >= max_cycles:
            break
        sleep(max(0.0, interval - (mono() - started)))
    return stats
