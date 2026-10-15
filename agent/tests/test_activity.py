import pytest

from akwaba_agent.aggregator.activity import ActivityTracker


def test_streak_grows_and_resets():
    t = ActivityTracker(min_replied=3, required_windows=2)
    assert t.update({"a": 3, "b": 2}) == {"a": 1}
    assert t.update({"a": 5, "b": 3}) == {"a": 2, "b": 1}
    assert t.update({"a": 2, "b": 9}) == {"b": 2}
    assert t.update({"a": 3}) == {"a": 1}          # a repart de zéro
    assert t.update({}) == {}


def test_reset_and_validation():
    t = ActivityTracker()
    t.update({"a": 9})
    t.reset()
    assert t.update({"a": 9}) == {"a": 1}
    for bad in ((0, 2), (3, 0)):
        with pytest.raises(ValueError):
            ActivityTracker(*bad)
