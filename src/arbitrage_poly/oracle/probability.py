"""Terminal UP/DOWN probability models."""

from __future__ import annotations

import math

from .volatility import SECONDS_PER_YEAR

MODEL_NAME = "terminal_lognormal_ewma"

def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def terminal_prob_up(
    *,
    current_price: float,
    reference_price: float,
    remaining_ns: int,
    volatility: float,
    reversion_speed: float = 0.0,
) -> float:
    """Return P(terminal price > reference price) under zero-drift lognormality.

    `reversion_speed` (kappa, per year) is an optional Ornstein-Uhlenbeck-style
    adjustment: the observed log deviation from the reference price is decayed
    by `exp(-reversion_speed * tau_years)` before being scored, modeling a
    market expectation that part of the current move fades before the
    terminal time. `reversion_speed=0.0` (default) reproduces the original
    pure zero-drift random walk with no reversion.
    """

    if current_price <= 0.0 or reference_price <= 0.0:
        raise ValueError("prices must be positive")
    if remaining_ns < 0 or volatility < 0.0:
        raise ValueError("remaining time and volatility must be non-negative")
    if reversion_speed < 0.0:
        raise ValueError("reversion_speed must be non-negative")
    if remaining_ns == 0 or volatility == 0.0:
        if current_price > reference_price:
            return 1.0
        if current_price < reference_price:
            return 0.0
        return 0.5

    tau_years = remaining_ns / 1_000_000_000 / SECONDS_PER_YEAR
    log_deviation = math.log(current_price / reference_price)
    if reversion_speed > 0.0:
        log_deviation *= math.exp(-reversion_speed * tau_years)
    z = log_deviation / (volatility * math.sqrt(tau_years))
    return min(1.0, max(0.0, _normal_cdf(z)))
