"""Realized volatility estimators used by the price oracle."""

from __future__ import annotations

import math
from collections import deque

from arbitrage_poly.models import Tick

SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60
NANOSECONDS_PER_SECOND = 1_000_000_000

TICK_EWMA = "tick_ewma"
HORIZON_EWMA = "horizon_ewma"
VOLATILITY_MODELS = (HORIZON_EWMA, TICK_EWMA)
DEFAULT_HORIZON_S = 60.0
DEFAULT_HALF_LIFE_S = 900.0
DEFAULT_WARMUP_S = 3600.0


class EwmaVolatility:
    """Estimate annualized volatility from midpoint log-returns."""

    def __init__(
        self,
        *,
        alpha: float = 0.1,
        floor: float = 1e-6,
        sampling_interval_ns: int | None = None,
    ) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        if floor < 0.0:
            raise ValueError("floor must be non-negative")
        if sampling_interval_ns is not None and sampling_interval_ns <= 0:
            raise ValueError("sampling_interval_ns must be positive")
        self.alpha = alpha
        self.floor = floor
        self.sampling_interval_ns = sampling_interval_ns
        self._previous: Tick | None = None
        self._previous_bucket: int | None = None
        self._variance_per_second = 0.0

    def update(self, tick: Tick) -> float:
        """Consume a tick and return the current annualized volatility."""

        if self.sampling_interval_ns is not None:
            bucket = tick.ts_ns // self.sampling_interval_ns
            if self._previous_bucket == bucket:
                return self.volatility
            self._previous_bucket = bucket

        if self._previous is None:
            self._previous = tick
            return self.floor

        elapsed_seconds = (tick.ts_ns - self._previous.ts_ns) / 1_000_000_000
        if elapsed_seconds <= 0.0:
            return self.volatility

        log_return = math.log(tick.price / self._previous.price)
        observation_variance = log_return * log_return / elapsed_seconds
        self._variance_per_second = (
            self.alpha * observation_variance
            + (1.0 - self.alpha) * self._variance_per_second
        )
        self._previous = tick
        return self.volatility

    @property
    def volatility(self) -> float:
        """Return annualized volatility with the configured numerical floor."""

        return max(self.floor, math.sqrt(self._variance_per_second * SECONDS_PER_YEAR))

    @property
    def ready(self) -> bool:
        return self._previous is not None


class HorizonEwmaVolatility:
    """EWMA of squared log returns over a fixed horizon, continuous across windows.

    Prices are sampled once per second; each sample contributes the return since the
    latest sample at least `horizon_s` older, normalized per second. Weights decay with
    elapsed time (`half_life_s`) and are bias-adjusted like pandas `ewm(adjust=True)`.
    """

    def __init__(
        self,
        *,
        horizon_s: float = DEFAULT_HORIZON_S,
        half_life_s: float = DEFAULT_HALF_LIFE_S,
        warmup_s: float = DEFAULT_WARMUP_S,
        floor: float = 1e-6,
        sampling_interval_ns: int = NANOSECONDS_PER_SECOND,
    ) -> None:
        if horizon_s <= 0.0 or half_life_s <= 0.0 or warmup_s < 0.0:
            raise ValueError("horizon and half-life must be positive, warmup non-negative")
        if floor < 0.0 or sampling_interval_ns <= 0:
            raise ValueError("floor must be non-negative and sampling interval positive")
        self.horizon_ns = int(horizon_s * NANOSECONDS_PER_SECOND)
        self.half_life_ns = half_life_s * NANOSECONDS_PER_SECOND
        self.warmup_ns = int(warmup_s * NANOSECONDS_PER_SECOND)
        self.floor = floor
        self.sampling_interval_ns = sampling_interval_ns
        self._samples: deque[tuple[int, float]] = deque()
        self._previous_bucket: int | None = None
        self._first_ts_ns: int | None = None
        self._last_update_ns: int | None = None
        self._weighted_sum = 0.0
        self._weight = 0.0

    def update(self, tick: Tick) -> float:
        """Consume a tick and return the current annualized volatility."""

        bucket = tick.ts_ns // self.sampling_interval_ns
        if bucket == self._previous_bucket or tick.price <= 0.0:
            return self.volatility
        self._previous_bucket = bucket
        if self._first_ts_ns is None:
            self._first_ts_ns = tick.ts_ns

        self._samples.append((tick.ts_ns, tick.price))
        horizon_start_ns = tick.ts_ns - self.horizon_ns
        while len(self._samples) > 1 and self._samples[1][0] <= horizon_start_ns:
            self._samples.popleft()
        base_ts_ns, base_price = self._samples[0]
        if base_ts_ns > horizon_start_ns:
            return self.volatility

        elapsed_s = (tick.ts_ns - base_ts_ns) / NANOSECONDS_PER_SECOND
        observation = math.log(tick.price / base_price) ** 2 / elapsed_s
        if self._last_update_ns is not None:
            decay = 0.5 ** ((tick.ts_ns - self._last_update_ns) / self.half_life_ns)
            self._weighted_sum *= decay
            self._weight *= decay
        self._weighted_sum += observation
        self._weight += 1.0
        self._last_update_ns = tick.ts_ns
        return self.volatility

    @property
    def volatility(self) -> float:
        """Return annualized volatility with the configured numerical floor."""

        if self._weight == 0.0:
            return self.floor
        variance_per_second = self._weighted_sum / self._weight
        return max(self.floor, math.sqrt(variance_per_second * SECONDS_PER_YEAR))

    @property
    def ready(self) -> bool:
        """True once enough history has been seen for a trustworthy estimate."""

        return (
            self._last_update_ns is not None
            and self._first_ts_ns is not None
            and self._last_update_ns - self._first_ts_ns >= self.warmup_ns
        )
