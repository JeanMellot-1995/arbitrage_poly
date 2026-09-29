"""Dynamic and fixed sizing for an already-accepted binary opportunity.

Sizing never reopens the buy decision made upstream (directional gate then
net edge). A rejected opportunity or an active kill switch always yields
`Q = 0` without evaluating the Kelly fraction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

SizingMode = Literal["fixed", "dynamic"]

REJECTED_REASONS = frozenset({"opportunity_rejected", "kill_switch_active", "below_min_stake"})


@dataclass(frozen=True, slots=True)
class SizingConfig:
    """Risk envelope shared by the fixed-stake baseline and dynamic sizing."""

    mode: SizingMode
    fixed_stake_usd: float | None = None
    bankroll: float | None = None
    kelly_fraction_cap: float = 0.5
    max_stake_per_market: float = 20.0
    min_stake_usd: float = 1.0
    tick_size: float = 0.01
    kill_switch_active: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("fixed", "dynamic"):
            raise ValueError("mode must be 'fixed' or 'dynamic'")
        if self.mode == "fixed" and (self.fixed_stake_usd is None or self.fixed_stake_usd <= 0.0):
            raise ValueError("fixed_stake_usd must be positive in fixed mode")
        if self.mode == "dynamic" and (self.bankroll is None or self.bankroll <= 0.0):
            raise ValueError("bankroll must be positive in dynamic mode")
        if not 0.0 < self.kelly_fraction_cap <= 1.0:
            raise ValueError("kelly_fraction_cap must be in (0, 1]")
        if self.max_stake_per_market <= 0.0:
            raise ValueError("max_stake_per_market must be positive")
        if self.min_stake_usd < 0.0:
            raise ValueError("min_stake_usd must be non-negative")
        if self.min_stake_usd > self.max_stake_per_market:
            raise ValueError("min_stake_usd must not exceed max_stake_per_market")
        if self.tick_size <= 0.0:
            raise ValueError("tick_size must be positive")


@dataclass(frozen=True, slots=True)
class SizingDecision:
    """Deterministic, auditable outcome of a sizing evaluation."""

    stake_usd: float
    mode: SizingMode
    kelly_fraction: float | None = None
    applied_cap: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.stake_usd < 0.0:
            raise ValueError("stake_usd must be non-negative")
        if self.reason is not None and self.reason not in REJECTED_REASONS:
            raise ValueError(f"unknown sizing rejection reason: {self.reason}")
        if self.reason is not None and self.stake_usd != 0.0:
            raise ValueError("a rejected sizing decision must carry stake_usd = 0")


def _floor_to_tick(value: float, tick_size: float) -> float:
    return math.floor(value / tick_size + 1e-9) * tick_size


def compute_sizing(
    *,
    accepted: bool,
    config: SizingConfig,
    probability: float | None = None,
    price: float | None = None,
) -> SizingDecision:
    """Size an already-accepted opportunity; never reopens the buy decision.

    `probability` and `price` are required in dynamic mode and ignored in
    fixed mode. They must both be in (0, 1) when provided.
    """
    if not accepted:
        return SizingDecision(stake_usd=0.0, mode=config.mode, reason="opportunity_rejected")
    if config.kill_switch_active:
        return SizingDecision(stake_usd=0.0, mode=config.mode, reason="kill_switch_active")

    if config.mode == "fixed":
        assert config.fixed_stake_usd is not None
        raw_stake = config.fixed_stake_usd
        applied_cap = "max_stake_per_market" if raw_stake > config.max_stake_per_market else None
        stake = min(raw_stake, config.max_stake_per_market)
        kelly_fraction = None
    else:
        if probability is None or price is None:
            raise ValueError("probability and price are required in dynamic mode")
        if not 0.0 < probability < 1.0:
            raise ValueError("probability must be in (0, 1)")
        if not 0.0 < price < 1.0:
            raise ValueError("price must be in (0, 1)")
        assert config.bankroll is not None
        b = (1.0 - price) / price
        kelly_fraction = max(0.0, (probability * (1.0 + b) - 1.0) / b)
        kelly_stake = config.bankroll * kelly_fraction * config.kelly_fraction_cap
        if kelly_stake <= config.max_stake_per_market:
            stake = kelly_stake
            applied_cap = "kelly_fraction_cap" if kelly_fraction > 0.0 else None
        else:
            stake = config.max_stake_per_market
            applied_cap = "max_stake_per_market"

    stake = _floor_to_tick(stake, config.tick_size)
    if stake < config.min_stake_usd:
        return SizingDecision(
            stake_usd=0.0, mode=config.mode, kelly_fraction=kelly_fraction, reason="below_min_stake"
        )
    return SizingDecision(
        stake_usd=stake, mode=config.mode, kelly_fraction=kelly_fraction, applied_cap=applied_cap
    )
