"""Shared immutable and operational models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Tick:
    """Normalized price observation emitted by an oracle reader."""

    ts_ns: int
    price: float
    qty: float
    source: str
    seq: int | None
    exchange_ts_ns: int | None = None
    received_ts_ns: int | None = None

    def __post_init__(self) -> None:
        if self.ts_ns <= 0:
            raise ValueError("ts_ns must be positive")
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.qty < 0:
            raise ValueError("qty must be non-negative")
        if not self.source:
            raise ValueError("source must not be empty")


@dataclass(frozen=True, slots=True)
class FairValue:
    """Terminal binary fair value produced by the price oracle."""

    ts_ns: int
    window_start_ns: int
    reference_price: float
    current_price: float
    prob_up: float
    prob_down: float
    model: str
    volatility: float
    remaining_ns: int
    prob_up_raw: float | None = None

    def __post_init__(self) -> None:
        if self.ts_ns <= 0 or self.window_start_ns < 0:
            raise ValueError("timestamps must be valid")
        if self.reference_price <= 0 or self.current_price <= 0:
            raise ValueError("prices must be positive")
        if not 0.0 <= self.prob_up <= 1.0 or not 0.0 <= self.prob_down <= 1.0:
            raise ValueError("probabilities must be between 0 and 1")
        if self.prob_up_raw is not None and not 0.0 <= self.prob_up_raw <= 1.0:
            raise ValueError("prob_up_raw must be between 0 and 1")
        if abs((self.prob_up + self.prob_down) - 1.0) > 1e-12:
            raise ValueError("probabilities must sum to one")
        if self.volatility < 0.0:
            raise ValueError("volatility must be non-negative")
        if self.remaining_ns < 0:
            raise ValueError("remaining_ns must be non-negative")
        if not self.model:
            raise ValueError("model must not be empty")


@dataclass(slots=True)
class ConnectionState:
    """Observable state of the oracle transport."""

    connected: bool = False
    last_message_ts_ns: int | None = None
    last_heartbeat_ts_ns: int | None = None
    reconnect_attempts: int = 0
    last_error: str | None = None
    sequence_gap_detected: bool = False


@dataclass(slots=True)
class FlowMetrics:
    """Health and loss metrics for one oracle reader."""

    message_count: int = 0
    tick_count: int = 0
    rejected_count: int = 0
    sequence_gap_count: int = 0
    reconnect_count: int = 0
    queue_depth: int = 0
    queue_overflow_count: int = 0
    last_tick_age_ms: float | None = None
    latency_ms: float | None = None
    _last_tick_ts_ns: int | None = field(default=None, repr=False)

    def observe_tick(self, tick: Tick, now_ns: int) -> None:
        self.tick_count += 1
        self._last_tick_ts_ns = tick.ts_ns
        self.latency_ms = max(0.0, (now_ns - (tick.exchange_ts_ns or now_ns)) / 1_000_000)

    def update_age(self, now_ns: int) -> None:
        if self._last_tick_ts_ns is not None:
            self.last_tick_age_ms = max(0.0, (now_ns - self._last_tick_ts_ns) / 1_000_000)
