"""Contrat agent -> backend : ce que l'échantillonneur de l'agent produit est accepté par le backend."""
import pathlib
import sys
import uuid
from datetime import datetime, timezone

import pytest

AGENT = pathlib.Path(__file__).resolve().parents[2] / "agent"
pytestmark = pytest.mark.skipif(not AGENT.exists(), reason="dépôt agent non monté")

from app.schemas.agent import BatchIn  # noqa: E402

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
T = {"nf_conntrack_tcp_timeout_established": 432000}
LINE = ("tcp 6 431990 ESTABLISHED src=%s dst=203.0.113.10 sport=%d dport=443 src=203.0.113.10 "
        "dst=192.168.1.99 sport=443 dport=%d")


def _sampler(resolver_status):
    sys.path.insert(0, str(AGENT))
    from akwaba_agent.collector.conntrack import ParseResult, parse_line
    from akwaba_agent.peers.status import StatusResult
    from akwaba_agent.resolver.whois import Identity, WhoisResult
    from akwaba_agent.sampler import Sampler

    flows = [parse_line(LINE % (ip, p, p)) for ip in ("100.76.142.103", "100.91.19.91") for p in (1, 2, 3)]

    class Resolver:
        whois_ok = True

        def resolve(self, ip):
            if resolver_status == "RESOLVED":
                return WhoisResult("RESOLVED", Identity("User-A@Example.TEST", "Nom", 7, "nX" + ip[-2:], "host", True))
            return WhoisResult("UNRESOLVED", technical_error=True)

    return Sampler(read=lambda: ParseResult(flows, len(flows), 0), timeouts=lambda: dict(T),
                   status=lambda: StatusResult(ok=False), resolver=Resolver())


@pytest.mark.parametrize("status", ["RESOLVED", "UNRESOLVED"])
def test_the_real_agent_payload_is_accepted_for_the_first_and_following_cycles(status):
    sampler = _sampler(status)
    for _ in range(3):
        payload = sampler.sample(NOW).payload
        batch = BatchIn.model_validate({**payload, "batch_id": str(uuid.uuid4())})
        assert len(batch.observations) == 2
    assert batch.observations[0].new_flows == 0
