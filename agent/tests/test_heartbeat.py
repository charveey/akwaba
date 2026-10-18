import json
import os
import stat

from akwaba_agent.heartbeat import TransitionLogger, build_heartbeat, read_conntrack_load, write_atomic
from akwaba_agent.runner import LoopStats
from akwaba_agent.sampler import Health, SampleResult
from akwaba_agent.spool.store import Spool

NOW = 1_760_000_000.0


def res(ok=True, status=True, whois=True, n=2):
    return SampleResult(Health(conntrack_ok=ok, status_ok=status, whois_ok=whois, lines=100, unparsed=1,
                               timeouts_loaded=33, warnings=["a"]), {"observations": []}, n)


def test_conntrack_load(tmp_path):
    (tmp_path / "nf_conntrack_count").write_text("3840\n")
    (tmp_path / "nf_conntrack_max").write_text("7680\n")
    assert read_conntrack_load(str(tmp_path)) == (3840, 7680)


def test_conntrack_load_unavailable(tmp_path):
    assert read_conntrack_load(str(tmp_path)) is None
    (tmp_path / "nf_conntrack_count").write_text("abc")
    (tmp_path / "nf_conntrack_max").write_text("7680")
    assert read_conntrack_load(str(tmp_path)) is None
    (tmp_path / "nf_conntrack_count").write_text("10")
    (tmp_path / "nf_conntrack_max").write_text("0")
    assert read_conntrack_load(str(tmp_path)) is None


def test_heartbeat_fields(tmp_path):
    spool = Spool(str(tmp_path / "s" / "a.db"))
    spool.enqueue("x", NOW - 120)
    stats = LoopStats(cycles=5, errors=1, last_error="OSError", last_cycle_at=NOW)
    hb = build_heartbeat(NOW, res(), stats, spool, (3840, 7680), 0.8, "0.1.0-dev")
    assert (hb["conntrack_ok"], hb["status_ok"], hb["whois_ok"], hb["cycle_failed"]) == (True, True, True, False)
    assert (hb["lines"], hb["unparsed"], hb["timeouts_loaded"], hb["observations"]) == (100, 1, 33, 2)
    assert (hb["conntrack_entries"], hb["conntrack_max"], hb["conntrack_saturation"]) == (3840, 7680, 0.5)
    assert hb["conntrack_saturated"] is False
    assert (hb["spool_pending"], hb["spool_oldest_age_seconds"], hb["dropped_batches"]) == (1, 120.0, 0)
    assert (hb["cycles"], hb["loop_errors"], hb["last_error"]) == (5, 1, "OSError")
    assert hb["generated_at"].endswith("Z") and hb["last_cycle_at"].endswith("Z")
    json.dumps(hb)


def test_saturation_threshold_is_inclusive_and_load_may_be_missing():
    stats = LoopStats()
    assert build_heartbeat(NOW, res(), stats, None, (6144, 7680), 0.8, "v")["conntrack_saturated"] is True
    hb = build_heartbeat(NOW, res(), stats, None, None, 0.8, "v")
    assert hb["conntrack_entries"] is None and hb["conntrack_saturation"] is None
    assert hb["conntrack_saturated"] is False and hb["spool_pending"] is None


def test_failed_cycle_and_conntrack_failure():
    stats = LoopStats()
    failed = build_heartbeat(NOW, None, stats, None, None, 0.8, "v")
    assert failed["cycle_failed"] is True and failed["conntrack_ok"] is False and failed["observations"] == 0
    down = build_heartbeat(NOW, SampleResult(Health(conntrack_ok=False), None, 0), stats, None, None, 0.8, "v")
    assert down["cycle_failed"] is False and down["conntrack_ok"] is False


def test_dropped_batches_are_reported(tmp_path):
    spool = Spool(str(tmp_path / "s" / "a.db"), max_batches=1)
    for i in range(3):
        spool.enqueue("p%d" % i, float(i))
    assert build_heartbeat(NOW, res(), LoopStats(), spool, None, 0.8, "v")["dropped_batches"] == 2


def test_heartbeat_holds_nothing_personal():
    hb = build_heartbeat(NOW, res(), LoopStats(), None, None, 0.8, "v")
    assert not [k for k in hb for w in ("login", "identity", "src_ip", "hostname", "dst") if w in k]


def hb(**over):
    base = {"conntrack_ok": True, "status_ok": True, "whois_ok": True, "conntrack_saturated": False,
            "dropped_batches": 0}
    base.update(over)
    return base


def test_transition_logger_only_speaks_on_change():
    t = TransitionLogger()
    first = t.changes(hb())
    assert len(first) == 1 and first[0].startswith("etat initial")
    assert t.changes(hb()) == []
    out = t.changes(hb(conntrack_ok=False))
    assert len(out) == 1 and "conntrack_ok=False" in out[0] and "status_ok" not in out[0]
    assert t.changes(hb(conntrack_ok=False)) == []
    out = t.changes(hb(conntrack_ok=False, dropped_batches=3))
    assert len(out) == 1 and "total=3" in out[0]
    assert t.changes(hb(conntrack_ok=False, dropped_batches=3)) == []


def test_write_atomic(tmp_path):
    path = str(tmp_path / "d" / "hb.json")
    write_atomic(path, {"a": 1})
    write_atomic(path, {"a": 2})
    assert json.load(open(path)) == {"a": 2}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert os.listdir(os.path.dirname(path)) == ["hb.json"]


def test_conntrack_error_is_a_category_only():
    from akwaba_agent.collector.conntrack import CollectorError, _run
    from types import SimpleNamespace

    def failing(msg):
        return lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr=msg)

    cases = [("sudo: effective uid is not 0, is /usr/bin/sudo on a file system", "sudo_denied"),
             ("sudo: a password is required", "sudo_denied"),
             ("conntrack v1.4.6: Operation not permitted", "exit_code")]
    for msg, cat in cases:
        try:
            _run(["conntrack"], failing(msg), 5)
        except CollectorError as exc:
            assert exc.category == cat
    h = Health(conntrack_ok=False, conntrack_error="sudo_denied")
    hb = build_heartbeat(NOW, SampleResult(h, None, 0), LoopStats(), None, None, 0.8, "v")
    assert hb["conntrack_error"] == "sudo_denied"
    assert build_heartbeat(NOW, res(), LoopStats(), None, None, 0.8, "v")["conntrack_error"] is None
