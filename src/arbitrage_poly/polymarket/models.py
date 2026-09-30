"""Read-only contracts for Polymarket historical pricing at a decision offset."""

from __future__ import annotations

from dataclasses import dataclass

MARKET_LOOKUP_STATUSES = frozenset(
    {"found", "missing_market", "missing_token", "ambiguous_market", "api_error"}
)
TOKEN_PRICE_STATUSES = frozenset({"priced", "missing_price", "stale_price", "api_error"})
ECONOMIC_STATUSES = frozenset(
    {"priced", "missing_market", "missing_token", "missing_price", "stale_price", "api_error"}
)


@dataclass(frozen=True, slots=True)
class PolymarketMarket:
    """A binary Polymarket market resolved to its UP/DOWN token ids."""

    market_id: str
    up_token_id: str
    down_token_id: str
    question: str | None = None

    def __post_init__(self) -> None:
        if not self.market_id:
            raise ValueError("market_id must not be empty")
        if not self.up_token_id or not self.down_token_id:
            raise ValueError("up_token_id and down_token_id must not be empty")
        if self.up_token_id == self.down_token_id:
            raise ValueError("up_token_id and down_token_id must differ")


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A single historical price observation for one token."""

    ts_ns: int
    price: float
    source: str

    def __post_init__(self) -> None:
        if self.ts_ns <= 0:
            raise ValueError("ts_ns must be positive")
        if not 0.0 <= self.price <= 1.0:
            raise ValueError("price must be between 0 and 1")
        if not self.source:
            raise ValueError("source must not be empty")


@dataclass(frozen=True, slots=True)
class MarketLookupResult:
    """Outcome of a Gamma market discovery attempt for one window."""

    status: str
    market: PolymarketMarket | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in MARKET_LOOKUP_STATUSES:
            raise ValueError(f"unknown market lookup status: {self.status}")
        if self.status == "found" and self.market is None:
            raise ValueError("a found lookup must carry a market")


@dataclass(frozen=True, slots=True)
class TokenPriceLookup:
    """Outcome of a causal historical price lookup for one token."""

    status: str
    price_point: PricePoint | None = None
    age_ns: int | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in TOKEN_PRICE_STATUSES:
            raise ValueError(f"unknown token price status: {self.status}")
        if self.status == "priced" and (self.price_point is None or self.age_ns is None):
            raise ValueError("a priced lookup must carry a price point and its age")


@dataclass(frozen=True, slots=True)
class EconomicWindowPricing:
    """Polymarket UP/DOWN prices resolved for one Oracle window at T-120s by default.

    A non-`priced` status means the window must be excluded from edge, sizing
    and P/L simulation; it does not affect the Oracle's own FairValue score.
    """

    window_start_ns: int
    decision_ts_ns: int
    status: str
    market_id: str | None = None
    up_token_id: str | None = None
    down_token_id: str | None = None
    up_price: float | None = None
    down_price: float | None = None
    up_price_ts_ns: int | None = None
    down_price_ts_ns: int | None = None
    up_price_age_ns: int | None = None
    down_price_age_ns: int | None = None
    price_source: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ECONOMIC_STATUSES:
            raise ValueError(f"unknown economic status: {self.status}")
        if self.status == "priced" and (self.up_price is None or self.down_price is None):
            raise ValueError("a priced window must carry both up_price and down_price")

    @property
    def is_priced(self) -> bool:
        return self.status == "priced"
