import json

from akwaba_agent.runner import LoopStats, run_loop
from akwaba_agent.sampler import Health, SampleResult
from akwaba_agent.spool.store import Spool


class Fake:
    def __init__(self, results):
        self.results = list(results)

    def sample(self, now):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def ok(n=1):
    return SampleResult(Health(conntrack_ok=True), {"observations": [{"i": i} for i in range(n)]}, n)


def test_only_non_empty_batches_are_spooled_and_loop_survives_errors(tmp_path):
    spool = Spool(str(tmp_path / "s" / "a.db"))
    sleeps = []
    stats = run_loop(Fake([ok(1), SampleResult(Health(), None, 0), RuntimeError("secret"), ok(0), ok(2)]),
                     spool, 60, sleep=sleeps.append, max_cycles=5)
    assert (stats.cycles, stats.enqueued, stats.errors, stats.last_error) == (5, 2, 1, "RuntimeError")
    assert spool.pending() == 2 and len(sleeps) == 4          # pas de sommeil après le dernier cycle
    assert len(json.loads(spool.peek(2)[1].payload)["observations"]) == 2


def test_sleep_compensates_for_cycle_duration():
    ticks = iter([0.0, 12.0, 12.0, 100.0, 100.0, 100.0])
    sleeps = []
    run_loop(Fake([ok(0), ok(0)]), None, 60, mono=lambda: next(ticks), wall=lambda: 1.0,
             sleep=sleeps.append, max_cycles=2)
    assert sleeps == [48.0]


def test_stop_flag_ends_the_loop_and_no_spool_is_allowed():
    flags = iter([False, False, True])
    stats = run_loop(Fake([ok(1), ok(1)]), None, 1, sleep=lambda s: None, should_stop=lambda: next(flags))
    assert stats.cycles == 2 and stats.enqueued == 0


def test_error_message_is_never_kept():
    stats = run_loop(Fake([ValueError("login=a@b.test")]), None, 1, sleep=lambda s: None, max_cycles=1)
    assert stats.last_error == "ValueError" and "a@b.test" not in repr(stats)


def test_on_cycle_gets_none_after_a_sampling_error_and_its_own_errors_are_contained():
    seen = []

    def on_cycle(res, spool):
        seen.append(res)
        raise RuntimeError("heartbeat")

    stats = LoopStats()
    run_loop(Fake([RuntimeError("x"), ok(1)]), None, 1, sleep=lambda s: None, max_cycles=2,
             on_cycle=on_cycle, stats=stats)
    assert seen[0] is None and seen[1] is not None
    assert (stats.cycles, stats.errors) == (2, 3) and stats.last_cycle_at is not None
