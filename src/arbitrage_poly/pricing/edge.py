"""Pure edge and executable-price calculations for binary Polymarket bets."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from arbitrage_poly.models import FairValue

Outcome = Literal["UP", "DOWN"]
ExecutionMode = Literal["passive", "aggressive"]

DEFAULT_TAKER_FEE_RATE = 0.0007


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: float
    quantity: float

    def __post_init__(self) -> None:
        if not 0.0 < self.price <= 1.0:
            raise ValueError("price must be in (0, 1]")
        if self.quantity <= 0.0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Executable asks and quoting bids for both binary outcomes."""

    ts_ns: int
    up_asks: tuple[OrderBookLevel, ...] = ()
    down_asks: tuple[OrderBookLevel, ...] = ()
    up_bids: tuple[OrderBookLevel, ...] = ()
    down_bids: tuple[OrderBookLevel, ...] = ()

    def __post_init__(self) -> None:
        if self.ts_ns <= 0:
            raise ValueError("ts_ns must be positive")
        for levels in (self.up_asks, self.down_asks):
            if any(
                levels[index].price > levels[index + 1].price for index in range(len(levels) - 1)
            ):
                raise ValueError("ask levels must be sorted by ascending price")
        for levels in (self.up_bids, self.down_bids):
            if any(
                levels[index].price < levels[index + 1].price for index in range(len(levels) - 1)
            ):
                raise ValueError("bid levels must be sorted by descending price")

    def asks_for(self, side: Outcome) -> tuple[OrderBookLevel, ...]:
        return self.up_asks if side == "UP" else self.down_asks

    def bids_for(self, side: Outcome) -> tuple[OrderBookLevel, ...]:
        return self.up_bids if side == "UP" else self.down_bids


@dataclass(frozen=True, slots=True)
class PricingConfig:
    min_net_edge: float = 0.01
    slippage_buffer: float = 0.0
    tick_size: float = 0.01
    execution_mode: ExecutionMode = "aggressive"
    maker_fee_rate: float = 0.0
    taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE
    max_snapshot_age_ns: int | None = None
    max_fair_value_age_ns: int | None = None

    def __post_init__(self) -> None:
        if self.min_net_edge < 0.0 or self.slippage_buffer < 0.0:
            raise ValueError("edge and slippage thresholds must be non-negative")
        if self.tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        if self.execution_mode not in ("passive", "aggressive"):
            raise ValueError("execution_mode must be passive or aggressive")
        if self.maker_fee_rate < 0.0 or self.taker_fee_rate < 0.0:
            raise ValueError("fee rates must be non-negative")
        if self.max_snapshot_age_ns is not None and self.max_snapshot_age_ns < 0:
            raise ValueError("max_snapshot_age_ns must be non-negative")
        if self.max_fair_value_age_ns is not None and self.max_fair_value_age_ns < 0:
            raise ValueError("max_fair_value_age_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class EdgeEstimate:
    side: Outcome
    fair_probability: float
    best_ask: float
    average_ask: float
    quantity: float
    available_quantity: float
    fee_rate: float
    fee_per_share: float
    slippage: float
    gross_edge: float
    net_edge: float
    max_limit_price: float
    limit_price: float
    quoted_price: float | None
    execution_mode: ExecutionMode


def _floor_to_tick(price: float, tick_size: float) -> float:
    return math.floor(price / tick_size + 1e-9) * tick_size


def _weighted_average(levels: tuple[OrderBookLevel, ...], quantity: float) -> tuple[float, float]:
    remaining = quantity
    notional = 0.0
    filled = 0.0
    for level in levels:
        amount = min(remaining, level.quantity)
        notional += amount * level.price
        filled += amount
        remaining -= amount
        if remaining <= 1e-12:
            break
    if filled < quantity - 1e-12:
        raise ValueError("insufficient_depth")
    return notional / filled, sum(level.quantity for level in levels)


def calculate_edge(
    fair_value: FairValue,
    snapshot: OrderBookSnapshot,
    *,
    side: Outcome,
    quantity: float,
    config: PricingConfig,
    execution_mode: ExecutionMode | None = None,
    fee_rate: float | None = None,
) -> EdgeEstimate:
    """Calculate depth-weighted net edge for one already-selected outcome."""

    if quantity <= 0.0:
        raise ValueError("quantity must be positive")
    fair_probability = fair_value.prob_up if side == "UP" else fair_value.prob_down
    levels = snapshot.asks_for(side)
    if not levels:
        raise ValueError("missing_side")
    average_ask, available_quantity = _weighted_average(levels, quantity)
    best_ask = levels[0].price
    mode = execution_mode or config.execution_mode
    rate = fee_rate if fee_rate is not None else (
        config.taker_fee_rate if mode == "aggressive" else config.maker_fee_rate
    )
    if rate < 0.0:
        raise ValueError("fee rate must be non-negative")

    max_limit_price = (fair_probability - config.slippage_buffer - config.min_net_edge) / (
        1.0 + rate
    )
    limit_price = _floor_to_tick(max_limit_price, config.tick_size)
    bids = snapshot.bids_for(side)
    quoted_price: float | None = None
    if bids:
        if mode == "passive":
            quoted_price = min(limit_price, bids[0].price + config.tick_size)
        else:
            quoted_price = limit_price
    fee_per_share = average_ask * rate
    slippage = max(0.0, average_ask - best_ask)
    gross_edge = fair_probability - average_ask
    net_edge = gross_edge - fee_per_share - config.slippage_buffer
    return EdgeEstimate(
        side=side,
        fair_probability=fair_probability,
        best_ask=best_ask,
        average_ask=average_ask,
        quantity=quantity,
        available_quantity=available_quantity,
        fee_rate=rate,
        fee_per_share=fee_per_share,
        slippage=slippage,
        gross_edge=gross_edge,
        net_edge=net_edge,
        max_limit_price=max_limit_price,
        limit_price=limit_price,
        quoted_price=quoted_price,
        execution_mode=mode,
    )