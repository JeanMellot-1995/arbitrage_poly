"""Read-only live Polymarket order-book snapshots."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from arbitrage_poly.models import FairValue
from arbitrage_poly.polymarket.discovery import GammaMarketDiscovery
from arbitrage_poly.polymarket.rest import PolymarketApiError, RateLimitedRestClient
from arbitrage_poly.pricing.edge import OrderBookLevel, OrderBookSnapshot

CLOB_BOOK_URL = "https://clob.polymarket.com/book"


def _levels(payload: Any, field: str, *, descending: bool) -> tuple[OrderBookLevel, ...]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get(field), list):
        raise ValueError(f"invalid order book response: {field}")
    parsed = [
        OrderBookLevel(price=float(item["price"]), quantity=float(item["size"]))
        for item in payload[field]
        if isinstance(item, Mapping)
    ]
    return tuple(sorted(parsed, key=lambda level: level.price, reverse=descending))


class PolymarketLiveBook:
    """Fetch both binary token books using read-only GET requests.

    This first live adapter deliberately polls the public CLOB endpoint. It
    can be replaced by a CLOB WebSocket adapter without changing the pricing
    or live orchestration contracts.
    """

    def __init__(
        self,
        *,
        discovery: GammaMarketDiscovery | None = None,
        client: RateLimitedRestClient | None = None,
        book_url: str = CLOB_BOOK_URL,
        refresh_interval_s: float = 1.0,
        monotonic: Any = time.monotonic,
    ) -> None:
        if refresh_interval_s < 0.0:
            raise ValueError("refresh_interval_s must be non-negative")
        self.discovery = discovery or GammaMarketDiscovery(closed=False)
        self.client = client or RateLimitedRestClient()
        self.book_url = book_url
        self.refresh_interval_s = refresh_interval_s
        self._monotonic = monotonic
        self._market_by_window: dict[int, tuple[str, str]] = {}
        self._snapshot_by_window: dict[int, OrderBookSnapshot] = {}
        self._snapshot_at: dict[int, float] = {}

    @property
    def market_by_window(self) -> dict[int, tuple[str, str]]:
        """UP/DOWN token ids discovered so far, keyed by window start."""
        return self._market_by_window

    def snapshot(
        self, fair_value: FairValue, *, symbol: str = "BTCUSDT"
    ) -> OrderBookSnapshot | None:
        """Return the current UP/DOWN book, or ``None`` on a read failure."""

        cached = self._snapshot_by_window.get(fair_value.window_start_ns)
        cached_at = self._snapshot_at.get(fair_value.window_start_ns)
        if (
            cached is not None
            and cached_at is not None
            and self._monotonic() - cached_at < self.refresh_interval_s
        ):
            return cached

        window_end_ns = fair_value.window_start_ns + 300 * 1_000_000_000
        tokens = self._market_by_window.get(fair_value.window_start_ns)
        if tokens is None:
            lookup = self.discovery.find_market_for_window(
                symbol=symbol,
                window_start_ns=fair_value.window_start_ns,
                window_end_ns=window_end_ns,
            )
            if lookup.market is None:
                return None
            tokens = (lookup.market.up_token_id, lookup.market.down_token_id)
            self._market_by_window[fair_value.window_start_ns] = tokens

        try:
            up_payload = self.client.get(self.book_url, {"token_id": tokens[0]})
            down_payload = self.client.get(self.book_url, {"token_id": tokens[1]})
            snapshot = OrderBookSnapshot(
                ts_ns=time.time_ns(),
                up_asks=_levels(up_payload, "asks", descending=False),
                down_asks=_levels(down_payload, "asks", descending=False),
                up_bids=_levels(up_payload, "bids", descending=True),
                down_bids=_levels(down_payload, "bids", descending=True),
            )
            self._snapshot_by_window[fair_value.window_start_ns] = snapshot
            self._snapshot_at[fair_value.window_start_ns] = self._monotonic()
            return snapshot
        except (PolymarketApiError, KeyError, TypeError, ValueError):
            return None
