"""UTC nanosecond clock backed by a monotonic source."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MonotonicClock:
    """Convert monotonic time to a stable UTC nanosecond timeline."""

    utc_base_ns: int
    monotonic_base_ns: int

    @classmethod
    def system(cls) -> MonotonicClock:
        monotonic_ns = time.monotonic_ns()
        return cls(
            utc_base_ns=time.time_ns(),
            monotonic_base_ns=monotonic_ns,
        )

    def now_ns(self) -> int:
        """Return corrected UTC nanoseconds without using a naive datetime."""

        return self.utc_base_ns + (time.monotonic_ns() - self.monotonic_base_ns)


def now_ns() -> int:
    """Return current UTC nanoseconds using a monotonic sample."""

    return MonotonicClock.system().now_ns()
