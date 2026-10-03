import pytest

from scout.sources.browser import PoliteTimer


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def timer(clock):
    return PoliteTimer(4.0, 9.0, rng=lambda: 0.5, sleep=clock.sleep, clock=clock.clock)


def test_first_wait_does_not_sleep():
    clock = FakeClock()
    timer(clock).wait()
    assert clock.slept == []


def test_second_wait_sleeps_the_rest_of_the_delay():
    clock = FakeClock()
    t = timer(clock)
    t.wait()
    clock.now += 2.0
    t.wait()
    assert clock.slept == [pytest.approx(4.5)]  # delay 6.5 minus 2 already passed


def test_no_sleep_after_a_long_gap():
    clock = FakeClock()
    t = timer(clock)
    t.wait()
    clock.now += 30.0
    t.wait()
    assert clock.slept == []


def test_gap_starts_after_the_page_has_loaded():
    clock = FakeClock()
    t = timer(clock)
    t.wait()
    clock.now += 5.0  # the page load takes 5 seconds
    t.mark()
    t.wait()
    assert clock.slept == [pytest.approx(6.5)]  # the full delay, not 6.5 minus the load time
