import copy

import pytest
from pydantic import ValidationError

from app.schemas.agent_heartbeat import HeartbeatIn


def details(**over):
    base = {"lines": 113, "unparsed": 0, "timeouts_loaded": 33, "observations": 2, "conntrack_saturated": False,
            "dropped_batches": 0, "spool_recovered_corruption": False, "cycles": 4, "loop_errors": 0,
            "cycle_failed": False, "conntrack_saturation": 0.015, "spool_oldest_age_seconds": 180.6,
            "sent_batches": 4, "rejected_batches": 0, "send_failures": 0, "last_send_error": None,
            "clock_skew_seconds": -1, "backend_reachable": True}
    base.update(over)
    return base


def good(**over):
    base = {"schema": 1, "agent_version": "0.1.0-dev", "generated_at": "2026-10-18T22:00:00Z",
            "conntrack_ok": True, "status_ok": True, "whois_ok": True, "bytes_available": False,
            "spool_depth": 3, "conntrack_count": 283, "conntrack_max": 7680, "last_error": None,
            "details": details()}
    base.update(over)
    return base


def test_a_good_heartbeat_is_accepted():
    hb = HeartbeatIn.model_validate(good())
    assert hb.details.clock_skew_seconds == -1 and hb.generated_at.tzinfo is not None


def test_a_degraded_heartbeat_without_optional_values_is_accepted():
    hb = HeartbeatIn.model_validate(good(
        conntrack_ok=False, conntrack_count=None, conntrack_max=None, last_error="sudo_denied",
        details=details(conntrack_saturation=None, spool_oldest_age_seconds=None, sent_batches=None,
                        rejected_batches=None, send_failures=None, clock_skew_seconds=None,
                        backend_reachable=None, cycle_failed=True, lines=0)))
    assert hb.last_error == "sudo_denied" and hb.details.backend_reachable is None


def test_integer_saturation_is_accepted():
    assert HeartbeatIn.model_validate(good(details=details(conntrack_saturation=1))).details.conntrack_saturation == 1


def bad_cases():
    yield "schema 2", good(schema=2)
    yield "extra field", {**good(), "bonus": 1}
    yield "extra detail field", good(details={**details(), "login": "a@b.test"})
    yield "naive date", good(generated_at="2026-10-18T22:00:00")
    yield "bool as string", good(conntrack_ok="true")
    yield "bool as int", good(conntrack_ok=1)
    yield "negative depth", good(spool_depth=-1)
    yield "depth as string", good(spool_depth="3")
    yield "max zero", good(conntrack_max=0)
    yield "error with spaces", good(last_error="secret login@x.test")
    yield "error too long", good(last_error="x" * 65)
    yield "version with space", good(agent_version="0.1 dev")
    yield "saturation > 1", good(details=details(conntrack_saturation=1.5))
    yield "negative age", good(details=details(spool_oldest_age_seconds=-1.0))
    yield "bool as count", good(details=details(cycles=True))
    yield "missing details", {k: v for k, v in good().items() if k != "details"}
    yield "missing required detail", good(details={k: v for k, v in details().items() if k != "cycles"})
    yield "send error with spaces", good(details=details(last_send_error="a b"))


@pytest.mark.parametrize("name, payload", list(bad_cases()), ids=[n for n, _ in bad_cases()])
def test_invalid_heartbeats_are_refused(name, payload):
    with pytest.raises(ValidationError):
        HeartbeatIn.model_validate(copy.deepcopy(payload))
