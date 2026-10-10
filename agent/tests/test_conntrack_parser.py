import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from akwaba_agent.collector.conntrack import (
    CollectorError, Flow, is_client_source, is_external_destination, parse_line, parse_output, read_conntrack,
)

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "conntrack_synthetic.txt"
TCP = ("tcp      6 431997 ESTABLISHED src=100.76.142.103 dst=203.0.113.10 sport=51000 dport=443 "
       "src=203.0.113.10 dst=192.168.1.99 sport=443 dport=51000 [ASSURED] mark=0 use=1")


def test_tcp_established():
    f = parse_line(TCP)
    assert (f.proto, f.state, f.timeout_remaining, f.src) == ("tcp", "ESTABLISHED", 431997, "100.76.142.103")
    assert f.src_is_client and f.egress and f.replied and f.assured


def test_unreplied_syn_sent_and_udp_without_state():
    f = parse_line("tcp 6 118 SYN_SENT src=100.76.142.103 dst=203.0.113.12 sport=1 dport=443 [UNREPLIED] "
                   "src=203.0.113.12 dst=192.168.1.99 sport=443 dport=1")
    assert f.state == "SYN_SENT" and not f.replied and not f.assured
    u = parse_line("udp 17 54 src=100.76.142.103 dst=203.0.113.13 sport=1 dport=443 src=203.0.113.13 "
                   "dst=192.168.1.99 sport=443 dport=1 [ASSURED] mark=0 use=1")
    assert u.state is None and u.assured and u.replied


def test_ipv6_source_is_normalized():
    f = parse_line("tcp 6 431990 ESTABLISHED src=fd7a:115c:a1e0:0:0:0:1234:5678 dst=2001:db8::10 sport=1 dport=443 "
                   "src=2001:db8::10 dst=fd7a:115c:a1e0::1234:5678 sport=443 dport=1 [ASSURED]")
    assert f.src == "fd7a:115c:a1e0::1234:5678" and f.src_is_client and f.egress


def test_icmp_without_ports():
    f = parse_line("icmp 1 25 src=100.76.142.103 dst=203.0.113.16 type=8 code=0 id=1 src=203.0.113.16 "
                   "dst=192.168.1.99 type=0 code=0 id=1 mark=0 use=1")
    assert f.proto == "icmp" and f.state is None and f.egress


def test_extended_format_prefix_is_accepted():
    assert parse_line("ipv4     2 " + TCP).src == "100.76.142.103"


@pytest.mark.parametrize("line", [
    "", "ceci n'est pas une ligne conntrack",
    "tcp 6 abc ESTABLISHED src=1.2.3.4 dst=5.6.7.8",
    "tcp 6 10 ESTABLISHED src=pas-une-ip dst=5.6.7.8",
    "tcp 6 10 ESTABLISHED sport=1 dport=2",
])
def test_unparseable_lines_return_none(line):
    assert parse_line(line) is None


def test_flow_keeps_no_destination_or_port():
    names = {f.name for f in dataclasses.fields(Flow)}
    assert not [n for n in names if "dst" in n or "dest" in n or "port" in n]


@pytest.mark.parametrize("ip, expected", [
    ("100.126.200.88", False), ("192.168.1.6", False), ("10.1.2.3", False), ("172.16.0.1", False),
    ("127.0.0.1", False), ("169.254.1.1", False), ("224.0.0.1", False), ("fe80::1", False),
    ("fd7a:115c:a1e0::1", False), ("203.0.113.10", True), ("8.8.8.8", True), ("172.32.0.1", True),
    ("2001:db8::10", True),
])
def test_external_destination(ip, expected):
    import ipaddress
    assert is_external_destination(ipaddress.ip_address(ip)) is expected


@pytest.mark.parametrize("ip, expected", [
    ("100.64.0.1", True), ("100.127.255.255", True), ("100.128.0.1", False), ("192.168.1.99", False),
    ("fd7a:115c:a1e0::1", True), ("fd7a:115c:a1e1::1", False),
])
def test_client_source(ip, expected):
    import ipaddress
    assert is_client_source(ipaddress.ip_address(ip)) is expected


def test_fixture_is_parsed_with_one_unrecognized_line():
    r = parse_output(FIXTURE.read_text(encoding="utf-8"))
    assert (r.lines, len(r.flows), r.unparsed) == (11, 10, 1)


def test_unrecognized_format_raises_and_empty_output_is_valid():
    with pytest.raises(CollectorError):
        parse_output("foo\nbar\n")
    r = parse_output("")
    assert (r.lines, r.flows, r.unparsed) == (0, [], 0)


def fake_run(responses):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        r = responses[cmd[-1]]
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(returncode=r[0], stdout=r[1], stderr=r[2])

    run.calls = calls
    return run


def test_read_conntrack_runs_both_families_with_prefix():
    run = fake_run({"ipv4": (0, TCP + "\n", ""), "ipv6": (0, "", "")})
    r = read_conntrack(prefix=("sudo", "-n"), run=run)
    assert run.calls == [["sudo", "-n", "conntrack", "-L", "-f", "ipv4"],
                         ["sudo", "-n", "conntrack", "-L", "-f", "ipv6"]]
    assert len(r.flows) == 1 and r.warnings == []


def test_ipv6_failure_is_a_warning_not_an_error():
    r = read_conntrack(run=fake_run({"ipv4": (0, TCP + "\n", ""), "ipv6": (1, "", "boom")}))
    assert len(r.flows) == 1 and r.warnings and "ipv6" in r.warnings[0]


@pytest.mark.parametrize("v4", [(1, "", "permission denied"), FileNotFoundError(), PermissionError()])
def test_ipv4_failure_is_an_error(v4):
    with pytest.raises(CollectorError):
        read_conntrack(run=fake_run({"ipv4": v4, "ipv6": (0, "", "")}))
