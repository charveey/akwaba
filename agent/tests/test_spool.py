import os
import stat

from akwaba_agent.spool.store import Spool


def make(tmp_path, **kw):
    return Spool(str(tmp_path / "spool" / "agent.db"), **kw)


def test_fifo_and_ack(tmp_path):
    s = make(tmp_path)
    a = s.enqueue('{"n":1}', 100.0)
    b = s.enqueue('{"n":2}', 101.0)
    assert [x.batch_id for x in s.peek(5)] == [a, b]
    assert s.ack(a) and not s.ack(a)
    assert [x.batch_id for x in s.peek(5)] == [b] and s.pending() == 1


def test_batch_id_is_idempotent(tmp_path):
    s = make(tmp_path)
    s.enqueue("x", 1.0, batch_id="fixed")
    s.enqueue("y", 2.0, batch_id="fixed")
    assert s.pending() == 1 and s.peek(1)[0].payload == "x"


def test_attempts_are_counted(tmp_path):
    s = make(tmp_path)
    bid = s.enqueue("x", 1.0)
    s.mark_attempt(bid)
    s.mark_attempt(bid)
    assert s.peek(1)[0].attempts == 2


def test_survives_restart(tmp_path):
    s = make(tmp_path)
    s.enqueue("persisted", 1.0)
    s.close()
    assert make(tmp_path).peek(1)[0].payload == "persisted"


def test_count_bound_drops_oldest_first(tmp_path):
    s = make(tmp_path, max_batches=3)
    ids = [s.enqueue("p%d" % i, float(i)) for i in range(5)]
    assert s.pending() == 3 and s.dropped_batches == 2
    assert [x.batch_id for x in s.peek(10)] == ids[2:]


def test_size_bound_drops_oldest_but_keeps_the_newest(tmp_path):
    s = make(tmp_path, max_bytes=100)
    for i in range(5):
        s.enqueue("x" * 60, float(i))
    assert s.pending() == 1 and s.dropped_batches == 4


def test_permissions_are_private(tmp_path):
    s = make(tmp_path)
    assert stat.S_IMODE(os.stat(s.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(s.path)).st_mode) == 0o700


def test_corrupt_file_is_quarantined_and_replaced(tmp_path):
    path = tmp_path / "spool" / "agent.db"
    path.parent.mkdir(mode=0o700)
    path.write_bytes(b"ceci n'est pas une base sqlite" * 50)
    s = Spool(str(path))
    assert s.recovered_corruption and s.pending() == 0
    s.enqueue("ok", 1.0)
    assert s.pending() == 1
    assert [p for p in os.listdir(path.parent) if ".corrupt-" in p]


def test_oldest_age(tmp_path):
    s = make(tmp_path)
    assert s.oldest_age(10.0) is None
    s.enqueue("a", 100.0)
    s.enqueue("b", 150.0)
    assert s.oldest_age(160.0) == 60.0
