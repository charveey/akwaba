import json
from datetime import datetime, timezone

from akwaba_agent.collector.conntrack import CollectorError, ParseResult, parse_line
from akwaba_agent.peers.status import Peer, StatusResult
from akwaba_agent.resolver.whois import Identity, WhoisResult
from akwaba_agent.sampler import Sampler

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
V4 = "100.76.142.103"
T = {"nf_conntrack_tcp_timeout_established": 432000, "nf_conntrack_tcp_timeout_syn_sent": 120}


def line(sport, src=V4, state="ESTABLISHED", remaining=431990, unreplied=False):
    flag = "[UNREPLIED] " if unreplied else ""
    return parse_line("tcp 6 %d %s src=%s dst=203.0.113.10 sport=%d dport=443 %ssrc=203.0.113.10 "
                      "dst=192.168.1.99 sport=443 dport=%d" % (remaining, state, src, sport, flag, sport))


class Feed:
    def __init__(self, flows=()):
        self.flows, self.fail = list(flows), False

    def __call__(self):
        if self.fail:
            raise CollectorError("conntrack indisponible")
        return ParseResult(flows=list(self.flows), lines=len(self.flows), unparsed=0)


class Resolver:
    def __init__(self, result=None):
        self.result, self.whois_ok = result or WhoisResult("UNRESOLVED"), True

    def resolve(self, ip):
        return self.result


def make(feed, resolver=None, status=None, **kw):
    return Sampler(read=feed, timeouts=lambda: dict(T),
                   status=status or (lambda: StatusResult(ok=False, error="x")),
                   resolver=resolver or Resolver(), **kw)


def obs(result):
    return result.payload["observations"][0]


def three():
    return [line(1), line(2), line(3)]


def test_new_flows_unknown_first_then_counted():
    feed = Feed(three())
    s = make(feed)
    first = obs(s.sample(NOW))
    assert first["new_flows"] is None and first["total_flows"] == 3
    feed.flows = three() + [line(4)]
    assert obs(s.sample(NOW))["new_flows"] == 1
    assert obs(s.sample(NOW))["new_flows"] == 0


def test_two_consecutive_windows_are_required():
    feed = Feed(three())
    s = make(feed)
    o1 = obs(s.sample(NOW))
    assert (o1["active_window"], o1["consecutive_active_windows"], o1["active_confirmed"]) == (True, 1, False)
    o2 = obs(s.sample(NOW))
    assert (o2["consecutive_active_windows"], o2["active_confirmed"]) == (2, True)
    feed.flows = [line(1), line(2)]                       # seulement 2 flux avec réponse
    o3 = obs(s.sample(NOW))
    assert (o3["active_window"], o3["consecutive_active_windows"], o3["active_confirmed"]) == (False, 0, False)


def test_unreplied_flows_never_make_a_source_active():
    feed = Feed([line(i, state="SYN_SENT", remaining=118, unreplied=True) for i in range(5)])
    o = obs(make(feed).sample(NOW))
    assert (o["recent_flows"], o["recent_replied_flows"], o["active_window"]) == (5, 0, False)


def test_conntrack_failure_gives_no_payload_and_resets_everything():
    feed = Feed(three())
    s = make(feed)
    s.sample(NOW)
    s.sample(NOW)
    feed.fail = True
    r = s.sample(NOW)
    assert r.payload is None and r.observations == 0 and not r.health.conntrack_ok and r.health.warnings
    feed.fail = False
    o = obs(s.sample(NOW))
    assert o["consecutive_active_windows"] == 1 and o["active_confirmed"] is False and o["new_flows"] is None


def test_unresolved_identity_is_reported_not_invented():
    res = Resolver(WhoisResult("UNRESOLVED", technical_error=True))
    o = obs(make(Feed(three()), resolver=res).sample(NOW))
    assert (o["identity_status"], o["identity"], o["identity_technical_error"]) == ("UNRESOLVED", None, True)


def test_identity_payload_is_minimal():
    ident = Identity("a@b.test", "Secret Name", 7, "nX", "secret-host", True)
    r = make(Feed(three()), resolver=Resolver(WhoisResult("RESOLVED", ident))).sample(NOW)
    assert obs(r)["identity"] == {"login_name": "a@b.test", "tailscale_user_id": 7,
                                  "stable_id": "nX", "is_sharee": True}
    text = json.dumps(r.payload)
    assert "Secret Name" not in text and "secret-host" not in text


def test_excluded_source_is_not_observed():
    feed = Feed(three() + [line(9, src="100.85.214.5")])
    r = make(feed, exclude_sources=["100.85.214.5"]).sample(NOW)
    assert [o["src_ip"] for o in r.payload["observations"]] == [V4]


def test_peer_information_is_informative_and_optional():
    peer = Peer("nX", [V4], True, False, None, None, None, None, None, True, False, False, False)
    ok = make(Feed(three()), status=lambda: StatusResult(ok=True, peers=[peer], by_ip={V4: peer})).sample(NOW)
    assert obs(ok)["peer"] == {"known": True, "online": True, "expired": False, "tagged": False}
    assert ok.health.status_ok
    down = make(Feed(three())).sample(NOW)
    assert obs(down)["peer"] is None and not down.health.status_ok and down.observations == 1


def test_payload_holds_no_destination_or_port():
    text = json.dumps(make(Feed(three())).sample(NOW).payload)
    assert "203.0.113" not in text and "dport" not in text and "sport" not in text
    assert make(Feed(three())).sample(NOW).payload["sampled_at"] == "2026-10-15T12:00:00Z"


def test_no_observation_when_nobody_goes_out():
    r = make(Feed([])).sample(NOW)
    assert r.health.conntrack_ok and r.observations == 0 and r.payload["observations"] == []
