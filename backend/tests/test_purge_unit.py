import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.jobs.purge import PurgeLoop, build_purge
from app.main import create_app
from app.services import purge as svc
from app.services.purge import PurgeReport, Retention, cutoffs

NOW = datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_default_cutoffs_follow_the_validated_durations():
    c = cutoffs(NOW, Retention())
    assert c["agent_heartbeats"] == NOW - timedelta(days=30)
    assert c["job_runs"] == NOW - timedelta(days=30)
    assert c["exit_node_observations"] == c["agent_batches"] == NOW - timedelta(days=90)
    assert c["exit_node_sessions"] == NOW - timedelta(days=365)


@pytest.mark.parametrize("kw", [
    {"heartbeats_days": 6}, {"job_runs_days": 0}, {"sessions_days": -1},
    {"observations_days": 13}, {"observations_days": 7},
])
def test_invalid_retention_is_refused(kw):
    with pytest.raises(ValueError):
        Retention(**kw)


def test_the_minimums_are_accepted():
    Retention(heartbeats_days=7, job_runs_days=7, observations_days=14, sessions_days=7)


def test_every_purged_table_has_a_cutoff_a_delete_and_a_count():
    assert set(svc.TABLES) == set(cutoffs(NOW, Retention())) == set(svc._DELETE) == set(svc._COUNT)


def test_the_business_history_tables_never_appear_in_the_purge_sql():
    forbidden = ("audit_logs", "enforcement_decisions", "quarantine_records", "identity_exemptions", "payments",
                 "subscriptions", "exit_node_identities", "exit_node_peers", "protected_identities", "members",
                 "client_identities", "expenses", "reminders")
    statements = " ".join(list(svc._DELETE.values()) + list(svc._COUNT.values()))
    assert not [name for name in forbidden if name in statements]


def test_observations_are_deleted_before_the_batches_that_reference_them():
    assert svc.TABLES.index("exit_node_observations") < svc.TABLES.index("agent_batches")


def test_the_report_exposes_counts_only():
    r = PurgeReport(deleted={"job_runs": 3, "agent_heartbeats": 2}, capped=True, errors=1, last_error="OSError")
    assert r.total == 5 and r.details() == {
        "deleted": {"job_runs": 3, "agent_heartbeats": 2}, "capped": True, "errors": 1, "last_error": "OSError"}


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=None):
        return [r.getMessage() for r in self.records if level is None or r.levelno == level]


@pytest.fixture
def logs():
    cap = Capture()
    lg = logging.getLogger("akwaba.purge")
    old = lg.level
    lg.setLevel(logging.DEBUG)
    lg.addHandler(cap)
    yield cap
    lg.removeHandler(cap)
    lg.setLevel(old)


def test_a_quiet_pass_logs_nothing_and_a_deletion_logs_counts(logs):
    PurgeLoop(lambda: PurgeReport(deleted={"job_runs": 0}), 3600).tick()
    assert logs.messages(logging.INFO) == []
    PurgeLoop(lambda: PurgeReport(deleted={"agent_heartbeats": 12, "job_runs": 0}, capped=True), 3600).tick()
    [line] = logs.messages(logging.INFO)
    assert "supprimé=12" in line and "agent_heartbeats=12" in line and "job_runs" not in line and "reste à purger" in line


def test_a_skipped_pass_is_not_a_failure(logs):
    loop = PurgeLoop(lambda: None, 3600)
    loop.tick()
    assert loop.failures == 0 and logs.messages(logging.ERROR) == []


def test_exceptions_are_contained_and_the_message_is_never_logged(logs):
    state = {"fail": True}

    def run():
        if state["fail"]:
            raise RuntimeError("secret-login@example.test")
        return PurgeReport()

    loop = PurgeLoop(run, 3600)
    for _ in range(11):
        loop.tick()
    errors = logs.messages(logging.ERROR)
    assert loop.failures == 11 and len(errors) == 2 and all("secret" not in e and "RuntimeError" in e for e in errors)
    state["fail"] = False
    loop.tick()
    assert loop.failures == 0 and any("rétablie" in m for m in logs.messages(logging.INFO))


def test_the_first_pass_is_deferred_by_two_minutes_and_stopping_is_prompt():
    calls = []
    loop = PurgeLoop(lambda: calls.append(1), interval=3600)
    assert loop._delay == 120.0
    loop.start()
    time.sleep(0.1)
    assert calls == []
    started = time.monotonic()
    loop.stop(timeout=2)
    assert time.monotonic() - started < 1 and not loop._thread.is_alive()


def settings(**over):
    base = dict(purge_enabled=False, purge_interval_seconds=3600, purge_batch_size=5000,
                retention_heartbeats_days=30, retention_job_runs_days=30, retention_observations_days=90,
                retention_sessions_days=365)
    base.update(over)
    return SimpleNamespace(**base)


def test_the_purge_is_off_by_default_and_built_only_when_enabled():
    assert build_purge(settings()) is None
    loop = build_purge(settings(purge_enabled=True, purge_interval_seconds=600))
    assert isinstance(loop, PurgeLoop) and loop._interval == 600 and loop._thread is None


def test_invalid_retention_prevents_building_the_loop():
    with pytest.raises(ValueError):
        build_purge(settings(purge_enabled=True, retention_observations_days=3))


def test_the_application_starts_and_stops_both_loops_in_order(monkeypatch):
    events = []

    def fake(name):
        return SimpleNamespace(start=lambda: events.append(("start", name)),
                               stop=lambda timeout: events.append(("stop", name, timeout)))

    monkeypatch.setattr("app.main.build_reconciler", lambda s: fake("reconciler"))
    monkeypatch.setattr("app.main.build_purge", lambda s: fake("purge"))
    with TestClient(create_app()):
        assert events == [("start", "reconciler"), ("start", "purge")]
    assert events[2:] == [("stop", "reconciler", 8), ("stop", "purge", 8)]


def test_the_application_runs_with_no_loop_at_all(monkeypatch):
    monkeypatch.setattr("app.main.build_reconciler", lambda s: None)
    monkeypatch.setattr("app.main.build_purge", lambda s: None)
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
