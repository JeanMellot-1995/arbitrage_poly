"""Read-only CLOB historical price lookups for Polymarket tokens.

Prices are selected causally: a point observed after the decision timestamp
must never be used, matching research.md decision 10.
"""

from __future__ import annotations

from typing import Any

from arbitrage_poly.polymarket.models import PricePoint, TokenPriceLookup
from arbitrage_poly.polymarket.rest import PolymarketApiError, RateLimitedRestClient

CLOB_PRICES_HISTORY_URL = "https://clob.polymarket.com/prices-history"
DEFAULT_LOOKBACK_NS = 5 * 60 * 1_000_000_000
DEFAULT_MAX_AGE_NS = 2 * 60 * 1_000_000_000
DEFAULT_MAX_PAGES = 5
DEFAULT_SOURCE_LABEL = "clob.prices-history"


def _extract_points(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        history = payload.get("history")
        if isinstance(history, list):
            return [point for point in history if isinstance(point, dict)]
    return []


def _to_price_point(raw: dict[str, Any], source: str) -> PricePoint | None:
    timestamp = raw.get("t")
    price = raw.get("p")
    if timestamp is None or price is None:
        return None
    try:
        ts_ns = int(float(timestamp) * 1_000_000_000)
        price_value = float(price)
    except (TypeError, ValueError):
        return None
    if ts_ns <= 0 or not 0.0 <= price_value <= 1.0:
        return None
    return PricePoint(ts_ns=ts_ns, price=price_value, source=source)


class ClobHistoricalPriceClient:
    """Fetches historical token prices and selects the last causal point."""

    def __init__(
        self,
        *,
        base_url: str = CLOB_PRICES_HISTORY_URL,
        client: RateLimitedRestClient | None = None,
        source_label: str = DEFAULT_SOURCE_LABEL,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self._base_url = base_url
        self._client = client or RateLimitedRestClient()
        self._source_label = source_label
        self._max_pages = max_pages

    def get_price_at_or_before(
        self,
        token_id: str,
        decision_ts_ns: int,
        *,
        lookback_ns: int = DEFAULT_LOOKBACK_NS,
        max_age_ns: int = DEFAULT_MAX_AGE_NS,
    ) -> TokenPriceLookup:
        start_ts_ns = max(0, decision_ts_ns - lookback_ns)
        points: list[PricePoint] = []
        cursor: str | None = None

        for _ in range(self._max_pages):
            params: dict[str, Any] = {
                "market": token_id,
                "startTs": start_ts_ns // 1_000_000_000,
                "endTs": decision_ts_ns // 1_000_000_000,
                "fidelity": 1,
            }
            if cursor is not None:
                params["cursor"] = cursor
            try:
                payload = self._client.get(self._base_url, params)
            except PolymarketApiError as exc:
                return TokenPriceLookup(status="api_error", detail=str(exc))

            for raw_point in _extract_points(payload):
                point = _to_price_point(raw_point, self._source_label)
                if point is not None:
                    points.append(point)

            cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
            if not cursor:
                break

        causal_points = [point for point in points if point.ts_ns <= decision_ts_ns]
        if not causal_points:
            return TokenPriceLookup(
                status="missing_price", detail="no price at or before decision timestamp"
            )

        latest = max(causal_points, key=lambda point: point.ts_ns)
        age_ns = decision_ts_ns - latest.ts_ns
        if age_ns > max_age_ns:
            return TokenPriceLookup(status="stale_price", price_point=latest, age_ns=age_ns)
        return TokenPriceLookup(status="priced", price_point=latest, age_ns=age_ns)
