from pathlib import Path

import pytest

from akwaba_agent.aggregator.flows import flow_age, summarize
from akwaba_agent.collector.conntrack import parse_line, parse_output
from akwaba_agent.collector.timeouts import load_timeouts, max_timeout

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "conntrack_synthetic.txt"
T = {
    "nf_conntrack_tcp_timeout_established": 432000, "nf_conntrack_tcp_timeout_syn_sent": 120,
    "nf_conntrack_tcp_timeout_syn_recv": 60, "nf_conntrack_tcp_timeout_fin_wait": 120,
    "nf_conntrack_tcp_timeout_close_wait": 60, "nf_conntrack_tcp_timeout_last_ack": 30,
    "nf_conntrack_tcp_timeout_time_wait": 120, "nf_conntrack_tcp_timeout_close": 10,
    "nf_conntrack_udp_timeout": 30, "nf_conntrack_udp_timeout_stream": 120,
    "nf_conntrack_generic_timeout": 600, "nf_conntrack_icmp_timeout": 30,
}
V4, V6 = "100.76.142.103", "fd7a:115c:a1e0::1234:5678"


@pytest.mark.parametrize("proto, state, assured, expected", [
    ("tcp", "ESTABLISHED", True, 432000), ("tcp", "SYN_SENT", False, 120), ("tcp", "SYN_SENT2", False, None),
    ("tcp", None, False, None), ("udp", None, True, 120), ("udp", None, False, 30),
    ("icmp", None, False, 30), ("gre", None, False, 600),
])
def test_max_timeout(proto, state, assured, expected):
    assert max_timeout(proto, state, assured, T) == expected


def test_max_timeout_is_none_without_discovered_values():
    assert max_timeout("tcp", "ESTABLISHED", True, {}) is None


def test_flow_age_and_inconsistent_timeout():
    ok = parse_line("tcp 6 431997 ESTABLISHED src=100.76.142.103 dst=203.0.113.10 sport=1 dport=2")
    assert flow_age(ok, T) == 3
    too_big = parse_line("tcp 6 500000 ESTABLISHED src=100.76.142.103 dst=203.0.113.10 sport=1 dport=2")
    assert flow_age(too_big, T) is None


def test_load_timeouts_reads_only_numeric_timeout_files(tmp_path):
    (tmp_path / "nf_conntrack_udp_timeout").write_text("30\n")
    (tmp_path / "nf_conntrack_tcp_timeout_established").write_text("432000\n")
    (tmp_path / "nf_conntrack_generic_timeout").write_text("pas un nombre")
    (tmp_path / "nf_conntrack_max").write_text("7680\n")
    assert load_timeouts(str(tmp_path)) == {
        "nf_conntrack_udp_timeout": 30, "nf_conntrack_tcp_timeout_established": 432000}


def flows():
    return parse_output(FIXTURE.read_text(encoding="utf-8")).flows


def test_summary_counts_only_client_to_external_flows():
    s = {x.src: x for x in summarize(flows(), T)}
    assert set(s) == {V4, V6}
    v4 = s[V4]
    assert (v4.total_flows, v4.recent_flows, v4.recent_replied_flows, v4.unknown_age_flows) == (6, 5, 3, 0)
    v6 = s[V6]
    assert (v6.total_flows, v6.recent_flows, v6.recent_replied_flows, v6.unknown_age_flows) == (1, 1, 1, 0)
    assert summarize(flows(), T)[0].src == V4  # trié par flux récents avec réponse


def test_a_shorter_window_keeps_fewer_recent_flows():
    v4 = {x.src: x for x in summarize(flows(), T, window_seconds=10)}[V4]
    assert (v4.recent_flows, v4.recent_replied_flows) == (3, 2)


def test_excluded_source_is_ignored():
    assert [x.src for x in summarize(flows(), T, exclude_sources=["100.76.142.103"])] == [V6]


def test_unknown_age_is_never_recent():
    v4 = {x.src: x for x in summarize(flows(), {})}[V4]
    assert (v4.recent_flows, v4.recent_replied_flows, v4.unknown_age_flows) == (0, 0, 6)


def test_invalid_window_is_rejected():
    with pytest.raises(ValueError):
        summarize(flows(), T, window_seconds=0)
