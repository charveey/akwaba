import json

from akwaba_agent.resolver.whois import WhoisResolver, parse_whois

SHAREE = json.dumps({
    "Node": {"StableID": "ni9XG14zML11CNTRL", "Hostinfo": {"Hostname": "device-of-shared-to-user", "ShareeNode": True}},
    "UserProfile": {"ID": 10796686909042743, "LoginName": "User-A@Example.TEST", "DisplayName": "Utilisateur A"},
}).encode()
INTERNAL = json.dumps({
    "Node": {"StableID": "nNv4aHK3SH11CNTRL", "Hostinfo": {"Hostname": "ubuntu"}},
    "UserProfile": {"ID": 6663452772783207, "LoginName": "owner@example.test"},
}).encode()


def test_sharee_node_is_parsed_and_login_lowercased():
    r = parse_whois(SHAREE)
    assert r.status == "RESOLVED" and r.identity.is_sharee is True
    assert r.identity.login_name == "user-a@example.test"
    assert (r.identity.stable_id, r.identity.hostname) == ("ni9XG14zML11CNTRL", "device-of-shared-to-user")
    assert r.identity.tailscale_user_id == 10796686909042743


def test_missing_sharee_key_means_internal():
    assert parse_whois(INTERNAL).identity.is_sharee is False


def test_missing_profile_or_login_is_unresolved_not_a_technical_error():
    for body in (b'{"Node": {}}', b'{"UserProfile": {"LoginName": "  "}}', b'{"UserProfile": null}'):
        r = parse_whois(body)
        assert r.status == "UNRESOLVED" and r.identity is None and not r.technical_error


def test_garbage_is_unresolved_with_technical_error():
    for body in (b"pas du json", b"\xff\xfe", b"[]"):
        r = parse_whois(body)
        assert r.status == "UNRESOLVED" and r.technical_error


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_resolved_result_is_cached_for_five_minutes():
    calls, clock = [], Clock()
    resolver = WhoisResolver(lambda ip: (calls.append(ip) or (200, SHAREE)), clock)
    resolver.resolve("100.1.1.1")
    clock.t = 299
    resolver.resolve("100.1.1.1")
    assert len(calls) == 1
    clock.t = 301
    resolver.resolve("100.1.1.1")
    assert len(calls) == 2


def test_unresolved_is_retried_after_thirty_seconds():
    calls, clock = [], Clock()
    resolver = WhoisResolver(lambda ip: (calls.append(ip) or (404, b"")), clock)
    resolver.resolve("100.1.1.1")
    clock.t = 29
    resolver.resolve("100.1.1.1")
    assert len(calls) == 1
    clock.t = 31
    resolver.resolve("100.1.1.1")
    assert len(calls) == 2


def test_socket_failure_never_raises_and_flags_whois_not_ok():
    def boom(ip):
        raise ConnectionRefusedError()

    resolver = WhoisResolver(boom, Clock())
    r = resolver.resolve("100.1.1.1")
    assert r.status == "UNRESOLVED" and r.technical_error and not resolver.whois_ok


def test_unknown_node_does_not_degrade_whois_ok_but_http_500_does():
    resolver = WhoisResolver(lambda ip: (404, b""), Clock())
    resolver.resolve("100.1.1.1")
    assert resolver.whois_ok
    resolver = WhoisResolver(lambda ip: (500, b""), Clock())
    resolver.resolve("100.1.1.1")
    assert not resolver.whois_ok


def test_each_ip_is_cached_independently():
    calls, clock = [], Clock()
    resolver = WhoisResolver(lambda ip: (calls.append(ip) or (200, INTERNAL)), clock)
    resolver.resolve("100.1.1.1")
    resolver.resolve("100.1.1.2")
    assert calls == ["100.1.1.1", "100.1.1.2"]
