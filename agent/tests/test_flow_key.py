from akwaba_agent.collector.conntrack import parse_line

BASE = ("tcp 6 431990 ESTABLISHED src=100.76.142.103 dst=203.0.113.10 sport=%d dport=443 "
        "src=203.0.113.10 dst=192.168.1.99 sport=443 dport=%d [ASSURED]")


def test_same_flow_same_key_different_flow_different_key():
    a, b, c = parse_line(BASE % (1000, 1000)), parse_line(BASE % (1000, 1000)), parse_line(BASE % (1001, 1001))
    assert a.key == b.key and a.key != c.key


def test_key_is_opaque():
    k = parse_line(BASE % (51000, 51000)).key
    assert len(k) == 16 and all(ch in "0123456789abcdef" for ch in k)
    assert "51000" not in k and "443" not in k


def test_destination_changes_the_key():
    other = BASE.replace("dst=203.0.113.10 sport=%d dport=443", "dst=203.0.113.11 sport=%d dport=443")
    assert parse_line(BASE % (1, 1)).key != parse_line(other % (1, 1)).key


def test_icmp_uses_the_identifier():
    a = parse_line("icmp 1 25 src=100.76.142.103 dst=203.0.113.16 type=8 code=0 id=1 src=203.0.113.16 "
                   "dst=192.168.1.99 type=0 code=0 id=1")
    b = parse_line("icmp 1 25 src=100.76.142.103 dst=203.0.113.16 type=8 code=0 id=2 src=203.0.113.16 "
                   "dst=192.168.1.99 type=0 code=0 id=2")
    assert a.key != b.key
