import json
import os

from akwaba_agent.heartbeat import (
    DETAIL_KEYS, TransitionLogger, build_heartbeat, read_conntrack_acct, to_wire,
)
from akwaba_agent.runner import LoopStats
from akwaba_agent.sampler import Health, SampleResult

NOW = 1_760_000_000.0
SENDER = {"sent_batches": 2, "rejected_batches": 1, "send_failures": 3, "last_send_error": "http_5xx",
          "clock_skew_seconds": -2, "backend_reachable": False}


def res(ok=True, error=None):
    return SampleResult(Health(conntrack_ok=ok, status_ok=ok, whois_ok=True, lines=100, unparsed=1,
                               timeouts_loaded=33, conntrack_error=error), {"observations": []}, 2 if ok else 0)


def test_wire_format_has_exactly_the_expected_keys_and_is_json():
    wire = to_wire(build_heartbeat(NOW, res(), LoopStats(cycles=3), None, (283, 7680), 0.8, "0.1.0-dev", SENDER, True))
    assert set(wire) == {"schema", "agent_version", "generated_at", "conntrack_ok", "status_ok", "whois_ok",
                         "bytes_available", "spool_depth", "conntrack_count", "conntrack_max", "last_error",
                         "details"}
    assert set(wire["details"]) == set(DETAIL_KEYS)
    assert wire["bytes_available"] is True and wire["conntrack_count"] == 283 and wire["spool_depth"] == 0
    assert wire["details"]["send_failures"] == 3 and wire["details"]["backend_reachable"] is False
    json.dumps(wire)


def test_wire_error_category_prefers_the_conntrack_error():
    stats = LoopStats(last_error="OSError")
    degraded = to_wire(build_heartbeat(NOW, res(False, "sudo_denied"), stats, None, None, 0.8, "v"))
    assert degraded["last_error"] == "sudo_denied" and degraded["conntrack_ok"] is False
    assert to_wire(build_heartbeat(NOW, res(), stats, None, None, 0.8, "v"))["last_error"] == "OSError"
    assert to_wire(build_heartbeat(NOW, res(), LoopStats(), None, None, 0.8, "v"))["last_error"] is None


def test_a_failed_cycle_and_a_missing_sender_still_produce_a_valid_wire_heartbeat():
    wire = to_wire(build_heartbeat(NOW, None, LoopStats(), None, None, 0.8, "v"))
    assert wire["conntrack_ok"] is False and wire["details"]["cycle_failed"] is True
    assert wire["details"]["sent_batches"] is None and wire["bytes_available"] is False


def test_the_wire_heartbeat_holds_nothing_personal():
    wire = to_wire(build_heartbeat(NOW, res(), LoopStats(), None, None, 0.8, "v", SENDER))
    flat = json.dumps(wire)
    assert not [w for w in ("login", "identity", "src_ip", "hostname", "dst", "@") if w in flat]


def test_read_conntrack_acct(tmp_path):
    assert read_conntrack_acct(str(tmp_path)) is False
    (tmp_path / "nf_conntrack_acct").write_text("0\n")
    assert read_conntrack_acct(str(tmp_path)) is False
    (tmp_path / "nf_conntrack_acct").write_text("1\n")
    assert read_conntrack_acct(str(tmp_path)) is True
    (tmp_path / "nf_conntrack_acct").write_text("abc")
    assert read_conntrack_acct(str(tmp_path)) is False


def hb(**over):
    base = {"conntrack_ok": True, "status_ok": True, "whois_ok": True, "conntrack_saturated": False,
            "backend_reachable": True, "dropped_batches": 0, "rejected_batches": 0}
    base.update(over)
    return base


def test_the_logger_reports_backend_reachability_and_rejected_batches():
    t = TransitionLogger()
    assert "backend_reachable=True" in t.changes(hb())[0]
    assert t.changes(hb()) == []
    out = t.changes(hb(backend_reachable=False))
    assert len(out) == 1 and "backend_reachable=False" in out[0] and "conntrack_ok" not in out[0]
    out = t.changes(hb(backend_reachable=False, rejected_batches=2))
    assert len(out) == 1 and "total=2" in out[0]
    assert t.changes(hb(backend_reachable=False, rejected_batches=2)) == []
