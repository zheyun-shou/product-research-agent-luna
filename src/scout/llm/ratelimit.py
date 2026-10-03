"""Keep requests under a requests-per-minute limit."""
import time


class RateLimiter:
    def __init__(self, rpm: int, sleep=time.sleep, clock=time.monotonic):
        self.interval = 60.0 / rpm
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._last + self.interval - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()
