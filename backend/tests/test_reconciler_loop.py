import logging
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.jobs.reconciler import ReconcilerLoop, build_reconciler
from app.main import create_app
from app.services.quarantine import PlannedAction, ReconcileReport


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
    lg = logging.getLogger("akwaba.reconciler")
    old = lg.level
    lg.setLevel(logging.DEBUG)
    lg.addHandler(cap)
    yield cap
    lg.removeHandler(cap)
    lg.setLevel(old)


def report(**kw):
    r = ReconcileReport()
    for key, value in kw.items():
        setattr(r, key, value)
    return r


def wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_quiet_cycle_logs_nothing_and_a_change_logs_counts_only(logs):
    ReconcilerLoop(lambda: report(evaluated=5), 60).tick()
    assert logs.messages(logging.INFO) == []
    planned = [PlannedAction("secret-login@example.test", "OPEN", None, "PENDING", None, "unauthorized_active")]
    ReconcilerLoop(lambda: report(opened=1, blocked=2, planned=planned), 60).tick()
    [line] = logs.messages(logging.INFO)
    assert "ouvertes=1" in line and "blocages_simulés=2" in line and "secret" not in line


def test_anomalies_and_errors_are_reported(logs):
    ReconcilerLoop(lambda: report(anomalies={"unblock_without_block": 2}), 60).tick()
    ReconcilerLoop(lambda: report(errors=1), 60).tick()
    lines = logs.messages(logging.INFO)
    assert len(lines) == 2 and "anomalies=2" in lines[0] and "erreurs=1" in lines[1]


def test_a_skipped_run_because_of_the_lock_is_not_a_failure(logs):
    loop = ReconcilerLoop(lambda: None, 60)
    loop.tick()
    assert loop.failures == 0 and logs.messages(logging.ERROR) == []


def test_exceptions_are_contained_counted_and_logged_sparingly(logs):
    state = {"fail": True}

    def run():
        if state["fail"]:
            raise RuntimeError("secret-login@example.test")
        return report()

    loop = ReconcilerLoop(run, 60)
    for _ in range(25):
        loop.tick()                                      # ne lève jamais
    assert loop.failures == 25
    errors = logs.messages(logging.ERROR)
    assert len(errors) == 3 and all("RuntimeError" in e and "secret" not in e for e in errors)    # 1er, 10e, 20e
    state["fail"] = False
    loop.tick()
    assert loop.failures == 0 and any("rétablie après 25" in m for m in logs.messages(logging.INFO))


def test_the_thread_runs_repeatedly_and_stops_promptly():
    calls = []
    loop = ReconcilerLoop(lambda: calls.append(1), interval=0.02, initial_delay=0)
    loop.start()
    assert wait_for(lambda: len(calls) >= 3)
    started = time.monotonic()
    loop.stop(timeout=2)
    assert time.monotonic() - started < 1 and not loop._thread.is_alive()
    settled = len(calls)
    time.sleep(0.1)
    assert len(calls) == settled                          # plus aucun passage après l'arrêt


def test_a_long_interval_does_not_delay_the_stop():
    loop = ReconcilerLoop(lambda: None, interval=3600, initial_delay=0)
    loop.start()
    time.sleep(0.1)
    started = time.monotonic()
    loop.stop(timeout=2)
    assert time.monotonic() - started < 1 and not loop._thread.is_alive()


def test_the_initial_delay_defers_the_first_run_and_stop_cancels_it():
    calls = []
    loop = ReconcilerLoop(lambda: calls.append(1), interval=1, initial_delay=30)
    loop.start()
    time.sleep(0.2)
    assert calls == []
    loop.stop(timeout=2)
    assert calls == [] and not loop._thread.is_alive()


def test_the_thread_is_a_daemon_and_cannot_be_started_twice():
    loop = ReconcilerLoop(lambda: None, interval=3600, initial_delay=30)
    loop.start()
    try:
        assert loop._thread.daemon is True
        with pytest.raises(RuntimeError):
            loop.start()
    finally:
        loop.stop(timeout=2)
    loop.stop(timeout=2)                                  # idempotent


def test_invalid_intervals_are_refused():
    for interval, delay in ((0, 0), (-1, 0), (60, -1)):
        with pytest.raises(ValueError):
            ReconcilerLoop(lambda: None, interval, delay)


def settings(**over):
    base = dict(reconciler_enabled=False, reconciler_interval_seconds=60, revocation_mode="dry_run",
                enforcer_enabled=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_the_loop_is_off_by_default():
    assert build_reconciler(settings()) is None


def test_the_loop_is_built_when_enabled_in_dry_run():
    loop = build_reconciler(settings(reconciler_enabled=True, reconciler_interval_seconds=30))
    assert isinstance(loop, ReconcilerLoop) and loop._interval == 30 and loop._thread is None


@pytest.mark.parametrize("over", [{"revocation_mode": "enforce"}, {"enforcer_enabled": True}])
def test_the_loop_refuses_to_start_outside_dry_run(over):
    with pytest.raises(RuntimeError):
        build_reconciler(settings(reconciler_enabled=True, **over))
    assert build_reconciler(settings(**over)) is None                  # éteinte : aucune exigence


def test_the_application_starts_and_stops_the_loop(monkeypatch):
    events = []
    fake = SimpleNamespace(start=lambda: events.append("start"), stop=lambda timeout: events.append(("stop", timeout)))
    monkeypatch.setattr("app.main.build_reconciler", lambda s: fake)
    with TestClient(create_app()):
        assert events == ["start"]
    assert events == ["start", ("stop", 8)]


def test_the_application_runs_without_a_loop(monkeypatch):
    monkeypatch.setattr("app.main.build_reconciler", lambda s: None)
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
