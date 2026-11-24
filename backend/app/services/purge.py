"""Purge des données techniques anciennes. Les tables d'historique métier ne sont jamais touchées.

Ordre : observations, puis lots (clé RESTRICT), puis heartbeats, job_runs et sessions fermées.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.clock import Clock
from app.models.system import JobRun

logger = logging.getLogger("akwaba.purge")

JOB_NAME = "purge"
LOCK_KEY = 7_415_002                       # verrou distinct de celui de la réconciliation
MIN_DAYS = 7
MIN_OBSERVATION_DAYS = 14                  # la réconciliation lit les observations des 7 derniers jours

TABLES = ("exit_node_observations", "agent_batches", "agent_heartbeats", "job_runs", "exit_node_sessions")

_DELETE = {
    "exit_node_observations":
        "DELETE FROM exit_node_observations WHERE id IN "
        "(SELECT id FROM exit_node_observations WHERE observed_at < :cut ORDER BY id LIMIT :n)",
    "agent_batches":
        "DELETE FROM agent_batches WHERE batch_id IN "
        "(SELECT a.batch_id FROM agent_batches a WHERE a.received_at < :cut AND NOT EXISTS "
        "(SELECT 1 FROM exit_node_observations o WHERE o.batch_id = a.batch_id) LIMIT :n)",
    "agent_heartbeats":
        "DELETE FROM agent_heartbeats WHERE id IN "
        "(SELECT id FROM agent_heartbeats WHERE received_at < :cut ORDER BY id LIMIT :n)",
    "job_runs":
        "DELETE FROM job_runs WHERE id IN (SELECT id FROM job_runs WHERE started_at < :cut ORDER BY id LIMIT :n)",
    "exit_node_sessions":
        "DELETE FROM exit_node_sessions WHERE id IN "
        "(SELECT id FROM exit_node_sessions WHERE closed_at IS NOT NULL AND last_seen_at < :cut LIMIT :n)",
}

# Mode plan : même sélection, sans suppression. Pour les lots, l'état visé est « aucune observation restante ».
_COUNT = {
    "exit_node_observations": "SELECT count(*) FROM exit_node_observations WHERE observed_at < :cut",
    "agent_batches":
        "SELECT count(*) FROM agent_batches a WHERE a.received_at < :cut AND NOT EXISTS "
        "(SELECT 1 FROM exit_node_observations o WHERE o.batch_id = a.batch_id AND o.observed_at >= :cut)",
    "agent_heartbeats": "SELECT count(*) FROM agent_heartbeats WHERE received_at < :cut",
    "job_runs": "SELECT count(*) FROM job_runs WHERE started_at < :cut",
    "exit_node_sessions":
        "SELECT count(*) FROM exit_node_sessions WHERE closed_at IS NOT NULL AND last_seen_at < :cut",
}


@dataclass(frozen=True)
class Retention:
    heartbeats_days: int = 30
    job_runs_days: int = 30
    observations_days: int = 90
    sessions_days: int = 365

    def __post_init__(self) -> None:
        for name in ("heartbeats_days", "job_runs_days", "observations_days", "sessions_days"):
            if getattr(self, name) < MIN_DAYS:
                raise ValueError("%s doit être >= %d" % (name, MIN_DAYS))
        if self.observations_days < MIN_OBSERVATION_DAYS:
            raise ValueError("observations_days doit être >= %d" % MIN_OBSERVATION_DAYS)


def cutoffs(now: datetime, retention: Retention) -> Dict[str, datetime]:
    observations = now - timedelta(days=retention.observations_days)
    return {
        "exit_node_observations": observations,
        "agent_batches": observations,
        "agent_heartbeats": now - timedelta(days=retention.heartbeats_days),
        "job_runs": now - timedelta(days=retention.job_runs_days),
        "exit_node_sessions": now - timedelta(days=retention.sessions_days),
    }


@dataclass
class PurgeReport:
    deleted: Dict[str, int] = field(default_factory=dict)
    capped: bool = False
    errors: int = 0
    last_error: Optional[str] = None

    @property
    def total(self) -> int:
        return sum(self.deleted.values())

    def details(self) -> dict:
        """Compteurs seulement."""
        return {"deleted": dict(self.deleted), "capped": self.capped, "errors": self.errors,
                "last_error": self.last_error}


def purge(db: Session, clock: Clock, *, retention: Optional[Retention] = None, batch_size: int = 5000,
          max_chunks: int = 20, apply: bool = True) -> PurgeReport:
    if batch_size < 1 or max_chunks < 1:
        raise ValueError("batch_size et max_chunks doivent être >= 1")
    cut = cutoffs(clock.now(), retention or Retention())
    report = PurgeReport()
    for table in TABLES:
        try:
            if not apply:
                report.deleted[table] = int(db.execute(text(_COUNT[table]), {"cut": cut[table]}).scalar_one())
                continue
            total = 0
            for _ in range(max_chunks):
                n = db.execute(text(_DELETE[table]), {"cut": cut[table], "n": batch_size}).rowcount
                db.commit()                              # un commit par paquet
                total += n
                if n < batch_size:
                    break
            else:
                report.capped = True                     # des paquets pleins jusqu'au bout : le reste attend
            report.deleted[table] = total
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            report.errors += 1
            report.last_error = type(exc).__name__
            report.deleted.setdefault(table, 0)
            logger.error("purge en échec pour une table : %s", type(exc).__name__)
    if not apply:
        db.rollback()
    return report


def _finish(maker, run_id: int, clock: Clock, status: str, details: Optional[dict], error: Optional[str]) -> None:
    with maker() as db:
        run = db.get(JobRun, run_id)
        run.status, run.finished_at, run.details, run.error = status, clock.now(), details, error
        db.commit()


def run_purge_job(maker, clock: Clock, *, retention: Optional[Retention] = None, batch_size: int = 5000,
                  max_chunks: int = 20, apply: bool = True) -> Optional[PurgeReport]:
    """Un passage, sous verrou. None si une autre purge est déjà en cours."""
    with maker() as probe:
        engine = probe.get_bind()
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    with conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar_one():
            return None
        try:
            if not apply:                                  # --plan : aucune écriture, pas même job_runs
                with maker() as db:
                    return purge(db, clock, retention=retention, batch_size=batch_size,
                                 max_chunks=max_chunks, apply=False)
            with maker() as db:
                run = JobRun(job_name=JOB_NAME, status="RUNNING", started_at=clock.now())
                db.add(run)
                db.commit()
                run_id = run.id
            with maker() as db:
                try:
                    report = purge(db, clock, retention=retention, batch_size=batch_size,
                                   max_chunks=max_chunks, apply=True)
                except Exception as exc:
                    db.rollback()
                    _finish(maker, run_id, clock, "FAILED", None, type(exc).__name__)
                    raise
            _finish(maker, run_id, clock, "FAILED" if report.errors else "SUCCESS", report.details(),
                    report.last_error)
            return report
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
