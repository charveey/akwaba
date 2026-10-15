import json
import subprocess
from datetime import timezone
from types import SimpleNamespace

from akwaba_agent.peers.status import StatusReader, parse_status, parse_time


def peer(**over):
    base = {"ID": "nABC", "TailscaleIPs": ["100.91.19.91", "fd7a:115c:a1e0::1"], "Online": True,
            "Active": False, "ShareeNode": True, "RxBytes": 10, "TxBytes": 20,
            "LastHandshake": "2026-10-15T10:00:00.123456789Z", "HostName": "secret-host"}
    base.update(over)
    return base


def doc(*peers):
    return json.dumps({"Peer": {"nodekey:%d" % i: p for i, p in enumerate(peers)},
                       "User": {"1": {"LoginName": "secret@example.test"}}})


def test_peer_fields_and_defaults():
    r = parse_status(doc(peer(), peer(ID="nXYZ", TailscaleIPs=["100.1.1.1"], ShareeNode=None)))
    assert r.ok and len(r.peers) == 2
    p = r.by_ip["100.91.19.91"]
    assert p.is_sharee and p.online and not p.active and not p.expired and not p.tagged
    assert (p.rx_bytes, p.tx_bytes, p.stable_id) == (10, 20, "nABC")
    assert r.by_ip["fd7a:115c:a1e0::1"] is p
    del_key = [x for x in r.peers if x.stable_id == "nXYZ"][0]
    assert del_key.is_sharee is False


def test_absent_sharee_and_expired_mean_false():
    raw = peer()
    del raw["ShareeNode"]
    p = parse_status(doc(raw)).peers[0]
    assert p.is_sharee is False and p.expired is False


def test_malformed_booleans_are_false_not_true():
    p = parse_status(doc(peer(ShareeNode="true", Online=1, Expired="yes"))).peers[0]
    assert (p.is_sharee, p.online, p.expired) == (False, False, False)


def test_tagged_peer():
    p = parse_status(doc(peer(Tags=["tag:exit"]))).peers[0]
    assert p.tagged is True


def test_no_personal_field_is_kept():
    from dataclasses import fields
    from akwaba_agent.peers.status import Peer
    names = {f.name for f in fields(Peer)}
    assert not names & {"hostname", "host_name", "dns_name", "login", "login_name", "user", "cur_addr", "addrs"}


def test_empty_tailnet_is_valid():
    r = parse_status('{"Peer": null}')
    assert r.ok and r.peers == []


def test_invalid_inputs_fail_cleanly():
    for text in ("pas du json", "[]", '{"Peer": []}', '{"Peer": "x"}'):
        r = parse_status(text)
        assert not r.ok and r.peers == []


def test_duplicate_ips_are_counted():
    r = parse_status(doc(peer(), peer(ID="nOther")))
    assert r.ok and r.duplicate_ips == 2


def test_parse_time():
    assert parse_time("0001-01-01T00:00:00Z") is None
    assert parse_time("") is None and parse_time(None) is None and parse_time("hier") is None
    t = parse_time("2026-10-15T10:00:00.123456789Z")
    assert t.tzinfo is not None and t.year == 2026 and t.microsecond == 123456
    assert parse_time("2026-10-15T10:00:00+00:00").utcoffset().total_seconds() == 0
    assert parse_time("2026-10-15T10:00:00.5+02:00").utcoffset().total_seconds() == 7200
    assert parse_time("2026-10-15T10:00:00Z").astimezone(timezone.utc).hour == 10


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def fake_run(stdout="{}", code=0, exc=None):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if exc:
            raise exc
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")

    run.calls = calls
    return run


def test_cache_lasts_fifteen_seconds_and_prefix_is_used():
    clock, run = Clock(), fake_run(doc(peer()))
    reader = StatusReader(prefix=("sudo", "-n"), run=run, clock=clock)
    reader.read()
    clock.t = 14
    reader.read()
    assert len(run.calls) == 1 and run.calls[0] == ["sudo", "-n", "tailscale", "status", "--json"]
    clock.t = 16
    reader.read()
    assert len(run.calls) == 2


def test_failures_never_raise():
    for run in (fake_run(code=1), fake_run(exc=FileNotFoundError()), fake_run(exc=PermissionError()),
                fake_run(exc=subprocess.TimeoutExpired("x", 1)), fake_run(stdout="garbage")):
        r = StatusReader(run=run, clock=Clock()).read()
        assert not r.ok and r.peers == []


def test_a_failure_does_not_keep_stale_peers_after_expiry():
    clock = Clock()
    answers = [doc(peer()), "garbage"]
    reader = StatusReader(run=lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=answers.pop(0), stderr=""),
                          clock=clock)
    assert reader.read().ok
    clock.t = 20
    r = reader.read()
    assert not r.ok and r.peers == []


def test_module_has_no_logging():
    import inspect
    from akwaba_agent.peers import status
    src = inspect.getsource(status)
    assert "logging" not in src and "print(" not in src
