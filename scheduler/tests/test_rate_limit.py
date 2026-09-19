import threading
import time

import config
import rate_limit


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _fake_clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(rate_limit.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(rate_limit.time, "sleep", clock.sleep)
    return clock


def test_no_pacing_when_the_delay_is_zero(monkeypatch):
    clock = _fake_clock(monkeypatch)

    for _ in range(5):
        rate_limit.wait_for_scoring_slot()

    assert clock.slept == []


def test_first_call_is_immediate_and_the_next_waits_out_the_spacing(monkeypatch):
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(config, "SCORE_REQUEST_DELAY_SECONDS", 21)

    rate_limit.wait_for_scoring_slot()
    assert clock.slept == []

    clock.now += 5  # 5 s later the next job wants to score
    rate_limit.wait_for_scoring_slot()
    assert clock.slept == [16]  # only the rest of the 21 s, not another full 21


def test_no_wait_when_enough_time_has_passed_anyway(monkeypatch):
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(config, "SCORE_REQUEST_DELAY_SECONDS", 21)

    rate_limit.wait_for_scoring_slot()
    clock.now += 60
    rate_limit.wait_for_scoring_slot()

    assert clock.slept == []


def test_concurrent_jobs_share_one_pace(monkeypatch):
    """Jobs in different threads must together stay within the limit."""
    monkeypatch.setattr(config, "SCORE_REQUEST_DELAY_SECONDS", 0.05)
    starts = []

    def worker():
        rate_limit.wait_for_scoring_slot()
        starts.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 4
    assert all(gap >= 0.04 for gap in gaps), gaps  # ~0.05 each, with timer slack
