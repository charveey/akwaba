import json

from akwaba_agent.heartbeat import build_heartbeat, to_wire
from akwaba_agent.protocol import H_NONCE, H_SIGNATURE, H_TIMESTAMP, verify
from akwaba_agent.runner import LoopStats
from akwaba_agent.sampler import Health, SampleResult
from akwaba_agent.sender.client import Reply, Sender
from akwaba_agent.spool.store import Spool

SECRET = bytes.fromhex("11" * 32)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Transport:
    def __init__(self, replies=(), clock=None, tick=0.0):
        self.replies, self.calls, self.clock, self.tick = list(replies), [], clock, tick

    def post(self, path, body, headers):
        self.calls.append((path, body, headers))
        if self.clock is not None:
            self.clock.t += self.tick
        r = self.replies.pop(0) if self.replies else Reply(200)
        if isinstance(r, Exception):
            raise r
        return r


def make(transport, clock=None, **kw):
    clock = clock or Clock()
    return Sender(transport, "agt_x", SECRET, wall=clock, mono=clock, rand=lambda: 0.5, **kw), clock


def spool_with(tmp_path, n=2):
    spool = Spool(str(tmp_path / "s" / "a.db"))
    ids = [spool.enqueue(json.dumps({"x": i}), float(i)) for i in range(n)]
    return spool, ids


def test_sends_oldest_first_adds_batch_id_signs_and_acks(tmp_path):
    spool, (a, b) = spool_with(tmp_path)
    t = Transport()
    s, _ = make(t)
    s.flush(spool)
    assert spool.pending() == 0 and s.sent_batches == 2 and s.backend_reachable is True
    bodies = [json.loads(c[1]) for c in t.calls]
    assert [x["batch_id"] for x in bodies] == [a, b] and bodies[0]["x"] == 0
    path, body, h = t.calls[0]
    assert path == "/api/agent/batches" and h["X-Akwaba-Agent"] == "agt_x" and h[H_TIMESTAMP] == "1000"
    assert verify(SECRET, "POST", path, body, h[H_TIMESTAMP], h[H_NONCE], h[H_SIGNATURE])
    assert len({c[2][H_NONCE] for c in t.calls}) == 2


def test_definitive_refusals_drop_the_batch_without_backoff(tmp_path):
    spool, _ = spool_with(tmp_path, 3)
    s, _ = make(Transport([Reply(422), Reply(409), Reply(200)]))
    s.flush(spool)
    assert spool.pending() == 0 and s.rejected_batches == 2 and s.sent_batches == 1 and not s.in_backoff


def test_413_is_also_definitive(tmp_path):
    spool, _ = spool_with(tmp_path, 1)
    s, _ = make(Transport([Reply(413)]))
    s.flush(spool)
    assert spool.pending() == 0 and s.rejected_batches == 1 and s.last_send_error == "rejected_413"


def test_retryable_failure_keeps_the_batch_and_backs_off_exponentially(tmp_path):
    spool, _ = spool_with(tmp_path, 2)
    t = Transport([Reply(500), Reply(500), Reply(500), Reply(200), Reply(200)])
    s, clock = make(t)
    s.flush(spool)
    assert spool.pending() == 2 and len(t.calls) == 1 and s.in_backoff and s.last_send_error == "http_5xx"
    assert spool.peek(1)[0].attempts == 1
    clock.t += 4.9
    s.flush(spool)
    assert len(t.calls) == 1                                  # toujours en attente (5 s)
    clock.t += 0.2
    s.flush(spool)
    assert len(t.calls) == 2                                  # deuxième échec : 10 s
    clock.t += 9.9
    s.flush(spool)
    assert len(t.calls) == 2
    clock.t += 0.2
    s.flush(spool)
    assert len(t.calls) == 3                                  # troisième échec : 20 s
    clock.t += 20.1
    s.flush(spool)
    assert spool.pending() == 0 and not s.in_backoff and s.send_failures == 3 and s.last_send_error is None


def test_backoff_is_capped(tmp_path):
    spool, _ = spool_with(tmp_path, 1)
    s, clock = make(Transport([Reply(503)] * 15))
    for _ in range(12):
        if s._retry_at:
            clock.t = s._retry_at + 0.1
        s.flush(spool)
    assert s._retry_at - clock.t <= 300.0001 and s.send_failures == 12


def test_failure_categories_never_carry_text(tmp_path):
    spool, _ = spool_with(tmp_path, 1)
    for reply, category, reachable in [
        (Reply(0, category="timeout"), "timeout", False), (Reply(0, category="network"), "network", False),
        (Reply(401), "http_401", True), (Reply(404), "http_404", True), (Reply(502), "http_5xx", True),
    ]:
        s, _ = make(Transport([reply]))
        s.flush(spool)
        assert (s.last_send_error, s.backend_reachable) == (category, reachable)


def test_a_transport_that_raises_is_treated_as_a_network_failure(tmp_path):
    spool, _ = spool_with(tmp_path, 1)
    s, _ = make(Transport([RuntimeError("boom")]))
    s.flush(spool)
    assert s.last_send_error == "network" and spool.pending() == 1


def test_corrupt_batches_are_dropped_and_counted(tmp_path):
    spool = Spool(str(tmp_path / "s" / "a.db"))
    spool.enqueue("pas du json", 1.0)
    spool.enqueue("[1, 2]", 2.0)
    t = Transport()
    s, _ = make(t)
    s.flush(spool)
    assert spool.pending() == 0 and s.rejected_batches == 2 and s.last_send_error == "corrupt_batch" and not t.calls


def test_catch_up_is_bounded_by_count_and_by_time(tmp_path):
    spool, _ = spool_with(tmp_path, 5)
    s, _ = make(Transport(), max_per_cycle=2)
    s.flush(spool)
    assert s.sent_batches == 2 and spool.pending() == 3
    spool2, _ = spool_with(tmp_path / "b", 5)
    clock = Clock()
    s2, _ = make(Transport(clock=clock, tick=15.0), clock=clock, budget_seconds=20.0)
    s2.flush(spool2)
    assert s2.sent_batches == 2


def test_clock_skew_is_read_from_the_date_header(tmp_path):
    spool, _ = spool_with(tmp_path, 1)
    s, _ = make(Transport([Reply(200, date="Thu, 01 Jan 1970 00:16:50 GMT")]))      # 1010 s, local 1000 s
    s.flush(spool)
    assert s.clock_skew_seconds == 10
    s2, _ = make(Transport([Reply(401, date="Thu, 01 Jan 1970 00:16:30 GMT")]))
    spool2, _ = spool_with(tmp_path / "b", 1)
    s2.flush(spool2)
    assert s2.clock_skew_seconds == -10                                              # mesuré même sur un 401
    s3, _ = make(Transport([Reply(200, date="pas une date")]))
    spool3, _ = spool_with(tmp_path / "c", 1)
    s3.flush(spool3)
    assert s3.clock_skew_seconds is None


def hb(sender):
    h = Health(conntrack_ok=True, status_ok=True, whois_ok=True, lines=10, timeouts_loaded=33)
    return build_heartbeat(1000.0, SampleResult(h, {"observations": []}, 1), LoopStats(cycles=2), None,
                           (100, 7680), 0.8, "0.1.0-dev", sender.stats())


def test_heartbeat_is_signed_sent_to_its_endpoint_and_matches_the_wire_format(tmp_path):
    t = Transport()
    s, _ = make(t)
    beat = hb(s)
    s.send_heartbeat(beat)
    path, body, headers = t.calls[0]
    assert path == "/api/agent/heartbeat" and json.loads(body) == to_wire(beat)
    assert verify(SECRET, "POST", path, body, headers[H_TIMESTAMP], headers[H_NONCE], headers[H_SIGNATURE])
    assert s.backend_reachable is True


def test_heartbeat_is_skipped_during_backoff_and_a_422_does_not_cause_one(tmp_path):
    spool, _ = spool_with(tmp_path, 1)
    t = Transport([Reply(500)])
    s, _ = make(t)
    s.flush(spool)
    s.send_heartbeat(hb(s))
    assert len(t.calls) == 1                                  # rien d'envoyé pendant l'attente
    s2, _ = make(Transport([Reply(422)]))
    s2.send_heartbeat(hb(s2))
    assert not s2.in_backoff and s2.last_send_error == "rejected_422"


def test_a_failed_heartbeat_starts_a_backoff(tmp_path):
    s, _ = make(Transport([Reply(0, category="timeout")]))
    s.send_heartbeat(hb(s))
    assert s.in_backoff and s.last_send_error == "timeout"


def test_ping_reports_success_and_failures(tmp_path):
    ok = json.dumps({"status": "ok", "exit_node": "stray", "server_time": 1000}).encode()
    s, _ = make(Transport([Reply(200, date="Thu, 01 Jan 1970 00:16:41 GMT", body=ok)]))
    r = s.ping()
    assert (r["ok"], r["exit_node"], r["server_time"], r["clock_skew_seconds"]) == (True, "stray", 1000, 1)
    s2, _ = make(Transport([Reply(401)]))
    assert (s2.ping()["ok"], s2.ping()["category"]) == (False, "http_401")


def test_stats_hold_only_counters_and_categories():
    s, _ = make(Transport())
    assert set(s.stats()) == {"sent_batches", "rejected_batches", "send_failures", "last_send_error",
                              "clock_skew_seconds", "backend_reachable"}
