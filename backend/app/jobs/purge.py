"""Boucle périodique de purge, éteinte par défaut (PURGE_ENABLED). Ne supprime que des données techniques."""
import logging
from typing import Optional

from app.db.session import get_sessionmaker
from app.domain.clock import SystemClock
from app.jobs.reconciler import ReconcilerLoop
from app.services import purge as svc

logger = logging.getLogger("akwaba.purge")


class PurgeLoop(ReconcilerLoop):
    """Même mécanique que la réconciliation (thread, arrêt rapide, aucune exception ne s'échappe)."""

    def __init__(self, run, interval: float, initial_delay: float = 120.0) -> None:
        super().__init__(run, interval, initial_delay)

    def tick(self) -> None:
        try:
            report = self._run()
        except Exception as exc:  # noqa: BLE001
            self.failures += 1
            if self.failures == 1 or self.failures % 10 == 0:
                logger.error("purge en échec (%s), %d échec(s) consécutif(s)", type(exc).__name__, self.failures)
            return
        if self.failures:
            logger.info("purge rétablie après %d échec(s)", self.failures)
            self.failures = 0
        if report is None:
            logger.debug("purge : une autre purge est en cours")
            return
        if report.total or report.errors:
            detail = " ".join("%s=%d" % (t, n) for t, n in report.deleted.items() if n)
            logger.info("purge : supprimé=%d %s%s erreurs=%d", report.total, detail,
                        " (reste à purger)" if report.capped else "", report.errors)


def build_purge(settings) -> Optional[PurgeLoop]:
    """None si l'interrupteur est éteint."""
    if not settings.purge_enabled:
        return None
    retention = svc.Retention(
        heartbeats_days=settings.retention_heartbeats_days, job_runs_days=settings.retention_job_runs_days,
        observations_days=settings.retention_observations_days, sessions_days=settings.retention_sessions_days)
    return PurgeLoop(
        run=lambda: svc.run_purge_job(get_sessionmaker(), SystemClock(), retention=retention,
                                      batch_size=settings.purge_batch_size),
        interval=settings.purge_interval_seconds,
    )
