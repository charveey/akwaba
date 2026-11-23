"""Boucle périodique de réconciliation, dans le processus du backend. Dry-run uniquement.

Éteinte par défaut (RECONCILER_ENABLED). Le verrou PostgreSQL de run_job empêche deux traitements simultanés.
Rien dans ce module n'agit sur le réseau.
"""
import logging
import threading
from typing import Callable, Optional

from app.db.session import get_sessionmaker
from app.domain.clock import SystemClock
from app.services import quarantine as svc

logger = logging.getLogger("akwaba.reconciler")


class ReconcilerLoop:
    def __init__(self, run: Callable[[], Optional[svc.ReconcileReport]], interval: float,
                 initial_delay: float = 10.0) -> None:
        if interval <= 0 or initial_delay < 0:
            raise ValueError("interval doit être > 0 et initial_delay >= 0")
        self._run, self._interval, self._delay = run, float(interval), float(initial_delay)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.failures = 0

    def tick(self) -> None:
        """Un passage. Ne lève jamais d'exception."""
        try:
            report = self._run()
        except Exception as exc:  # noqa: BLE001 : la boucle ne doit jamais mourir
            self.failures += 1
            if self.failures == 1 or self.failures % 10 == 0:
                logger.error("réconciliation en échec (%s), %d échec(s) consécutif(s)",
                             type(exc).__name__, self.failures)
            return
        if self.failures:
            logger.info("réconciliation rétablie après %d échec(s)", self.failures)
            self.failures = 0
        if report is None:
            logger.debug("réconciliation : un autre traitement est en cours")
            return
        if report.opened or report.changed or report.blocked or report.unblocked or report.errors or report.anomalies:
            logger.info(
                "réconciliation : ouvertes=%d changées=%d blocages_simulés=%d déblocages_simulés=%d "
                "anomalies=%d erreurs=%d",
                report.opened, report.changed, report.blocked, report.unblocked,
                sum(report.anomalies.values()), report.errors)

    def _loop(self) -> None:
        if self._stop.wait(self._delay):
            return
        while True:
            self.tick()
            if self._stop.wait(self._interval):
                return

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("déjà démarrée")
        self._thread = threading.Thread(target=self._loop, name="reconciler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)


def build_reconciler(settings) -> Optional[ReconcilerLoop]:
    """None si l'interrupteur est éteint. Refuse de s'allumer hors du mode dry-run."""
    if not settings.reconciler_enabled:
        return None
    if settings.revocation_mode != "dry_run" or settings.enforcer_enabled:
        raise RuntimeError("la réconciliation exige REVOCATION_MODE=dry_run et ENFORCER_ENABLED=false")
    return ReconcilerLoop(
        run=lambda: svc.run_job(get_sessionmaker(), SystemClock(), apply=True),
        interval=settings.reconciler_interval_seconds,
    )
