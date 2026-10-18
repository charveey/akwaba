import copy
import uuid

import pytest
from pydantic import ValidationError

from app.schemas.agent import BatchIn


def obs(**over):
    base = {
        "src_ip": "100.76.142.103", "total_flows": 10, "recent_flows": 8, "recent_replied_flows": 6,
        "unknown_age_flows": 1, "new_flows": 2, "active_window": True, "consecutive_active_windows": 2,
        "active_confirmed": True, "identity_status": "RESOLVED", "identity_technical_error": False,
        "identity": {"login_name": "User-A@Example.TEST", "tailscale_user_id": 7, "stable_id": "nX", "is_sharee": True},
        "peer": {"known": True, "online": True, "expired": False, "tagged": False},
    }
    base.update(over)
    return base


def good(**over):
    base = {"batch_id": str(uuid.uuid4()), "schema": 1, "sampled_at": "2026-10-15T12:00:00Z", "window_seconds": 60,
            "conntrack_ok": True, "status_ok": True, "whois_ok": True, "observations": [obs()]}
    base.update(over)
    return base


def test_a_good_batch_is_accepted_and_the_login_is_lowercased():
    b = BatchIn.model_validate(good())
    assert b.observations[0].identity.login_name == "user-a@example.test"
    assert b.sampled_at.tzinfo is not None and b.schema_version == 1


def test_first_sample_has_unknown_new_flows_and_unresolved_has_no_identity():
    b = BatchIn.model_validate(good(observations=[
        obs(new_flows=None), obs(src_ip="100.64.0.9", identity_status="UNRESOLVED", identity=None, peer=None)]))
    assert b.observations[0].new_flows is None and b.observations[1].identity is None


def test_empty_observations_are_valid():
    assert BatchIn.model_validate(good(observations=[])).observations == []


def bad_cases():
    yield "schema 2", good(schema=2)
    yield "schema bool", good(schema=True)
    yield "conntrack down", good(conntrack_ok=False)
    yield "naive date", good(sampled_at="2026-10-15T12:00:00")
    yield "window 0", good(window_seconds=0)
    yield "window float", good(window_seconds=60.5)
    yield "bad uuid", good(batch_id="pas-un-uuid")
    yield "extra top field", {**good(), "bonus": 1}
    yield "extra obs field", good(observations=[{**obs(), "dst_ip": "203.0.113.1"}])
    yield "non tailscale ip", good(observations=[obs(src_ip="203.0.113.10")])
    yield "private ip", good(observations=[obs(src_ip="192.168.1.99")])
    yield "garbage ip", good(observations=[obs(src_ip="x")])
    yield "duplicate ip", good(observations=[obs(), obs()])
    yield "resolved without identity", good(observations=[obs(identity=None)])
    yield "unresolved with identity", good(observations=[obs(identity_status="UNRESOLVED")])
    yield "replied > recent", good(observations=[obs(recent_replied_flows=9)])
    yield "recent > total", good(observations=[obs(recent_flows=11, recent_replied_flows=6)])
    yield "recent + unknown > total", good(observations=[obs(recent_flows=8, unknown_age_flows=3)])
    yield "negative count", good(observations=[obs(total_flows=-1)])
    yield "count as string", good(observations=[obs(total_flows="10")])
    yield "bool as count", good(observations=[obs(total_flows=True)])
    yield "blank login", good(observations=[obs(identity={"login_name": "  ", "is_sharee": True})])
    yield "login with space", good(observations=[obs(identity={"login_name": "a b@x.test", "is_sharee": True})])
    yield "sharee as string", good(observations=[obs(identity={"login_name": "a@x.test", "is_sharee": "true"})])
    yield "too many observations", good(observations=[
        obs(src_ip="100.64.%d.%d" % (i // 250, i % 250 + 1)) for i in range(501)])
    yield "unknown status", good(observations=[obs(identity_status="MAYBE")])


@pytest.mark.parametrize("name, payload", list(bad_cases()), ids=[n for n, _ in bad_cases()])
def test_invalid_batches_are_refused(name, payload):
    with pytest.raises(ValidationError):
        BatchIn.model_validate(copy.deepcopy(payload))


def test_validation_errors_can_be_reported_without_echoing_values():
    with pytest.raises(ValidationError) as exc:
        BatchIn.model_validate(good(observations=[obs(identity={"login_name": "secret login@x.test", "is_sharee": True})]))
    problems = [".".join(str(p) for p in e["loc"]) + " : " + e["type"] for e in exc.value.errors()]
    assert problems and "secret" not in str(problems)
