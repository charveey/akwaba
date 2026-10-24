"""Contrat agent -> backend pour le heartbeat et pour le corps réellement envoyé par le Sender."""
import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

AGENT = pathlib.Path(__file__).resolve().parents[2] / "agent"
pytestmark = pytest.mark.skipif(not AGENT.exists(), reason="dépôt agent non monté")

from app.schemas.agent import BatchIn  # noqa: E402
from app.schemas.agent_heartbeat import HeartbeatIn  # noqa: E402

NOW = datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc)
SECRET = bytes.fromhex("11" * 32)
LINE = ("tcp 6 431990 ESTABLISHED src=100.76.142.103 dst=203.0.113.10 sport=%d dport=443 src=203.0.113.10 "
        "dst=192.168.1.99 sport=443 dport=%d")


def _agent():
    sys.path.insert(0, str(AGENT))
    from akwaba_agent import heartbeat, runner, sampler
    from akwaba_agent.collector.conntrack import ParseResult, parse_line
    from akwaba_agent.peers.status import StatusResult
    from akwaba_agent.resolver.whois import Identity, WhoisResult
    from akwaba_agent.sender.client import Reply, Sender
    from akwaba_agent.spool.store import Spool
    return locals()


def _result(a, ok=True):
    h = a["sampler"].Health(conntrack_ok=ok, status_ok=ok, whois_ok=True, lines=100, unparsed=1, timeouts_loaded=33)
    return a["sampler"].SampleResult(h, {"observations": []}, 2 if ok else 0)


def test_the_real_heartbeat_is_accepted_in_every_shape(tmp_path):
    a = _agent()
    hb_mod, stats = a["heartbeat"], a["runner"].LoopStats(cycles=3, last_cycle_at=1_760_000_000.0)
    spool = a["Spool"](str(tmp_path / "s" / "a.db"))
    spool.enqueue("{}", 1_759_999_900.0)
    sender_stats = {"sent_batches": 2, "rejected_batches": 1, "send_failures": 3, "last_send_error": "http_5xx",
                    "clock_skew_seconds": -2, "backend_reachable": False}
    shapes = [
        hb_mod.build_heartbeat(1_760_000_000.0, _result(a), stats, spool, (283, 7680), 0.8, "0.1.0-dev", sender_stats, True),
        hb_mod.build_heartbeat(1_760_000_000.0, _result(a, ok=False), stats, None, None, 0.8, "0.1.0-dev"),
        hb_mod.build_heartbeat(1_760_000_000.0, None, stats, spool, (7000, 7680), 0.8, "0.1.0-dev", sender_stats),
    ]
    for hb in shapes:
        parsed = HeartbeatIn.model_validate(json.loads(json.dumps(hb_mod.to_wire(hb))))
        assert parsed.schema_version == 1
    assert parsed.details.conntrack_saturated is True and parsed.bytes_available is False


def test_the_body_really_sent_by_the_sender_is_a_valid_batch(tmp_path):
    a = _agent()
    flows = [a["parse_line"](LINE % (p, p)) for p in (1, 2, 3)]

    class Resolver:
        whois_ok = True

        def resolve(self, ip):
            return a["WhoisResult"]("RESOLVED", a["Identity"]("User-A@Example.TEST", "Nom", 7, "nX", "host", True))

    sampler = a["sampler"].Sampler(
        read=lambda: a["ParseResult"](flows, 3, 0), timeouts=lambda: {"nf_conntrack_tcp_timeout_established": 432000},
        status=lambda: a["StatusResult"](ok=False), resolver=Resolver())
    spool = a["Spool"](str(tmp_path / "s" / "a.db"))
    spool.enqueue(json.dumps(sampler.sample(NOW).payload, separators=(",", ":"), sort_keys=True), 1.0)

    seen = []

    class Transport:
        def post(self, path, body, headers):
            seen.append((path, body))
            return a["Reply"](200)

    sender = a["Sender"](Transport(), "agt_x", SECRET)
    sender.flush(spool)
    assert spool.pending() == 0 and seen[0][0] == "/api/agent/batches"
    batch = BatchIn.model_validate(json.loads(seen[0][1]))
    assert len(batch.observations) == 1 and str(batch.batch_id)
