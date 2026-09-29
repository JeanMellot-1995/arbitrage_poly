"""Read-only Gamma market discovery for historical Polymarket windows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from arbitrage_poly.polymarket.models import MarketLookupResult, PolymarketMarket
from arbitrage_poly.polymarket.rest import PolymarketApiError, RateLimitedRestClient

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
_UP_LABELS = {"up", "yes"}
_DOWN_LABELS = {"down", "no"}

# Confirmed against a live Gamma response: the slug encodes the window's
# start instant, shared by all duration variants of the same series
# (`btc-updown-{duration}-{window_start_unix_seconds}`). `endDate` is the
# true resolution instant (`window_end`); `startDate` is unrelated to the
# window (observed ~24h before resolution, likely market listing time) and
# must never be used to match a window.
_SLUG_DURATION_LABELS = {300: "5m", 900: "15m", 3600: "1h"}


def _parse_iso_ts_ns(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1_000_000_000)


def _parse_json_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _to_market(raw: dict[str, Any]) -> PolymarketMarket | None:
    market_id = raw.get("id") or raw.get("conditionId")
    token_ids = _parse_json_list(raw.get("clobTokenIds"))
    outcomes = _parse_json_list(raw.get("outcomes"))
    if not market_id or not token_ids or not outcomes or len(token_ids) != 2 or len(outcomes) != 2:
        return None

    up_token_id: str | None = None
    down_token_id: str | None = None
    for token_id, outcome in zip(token_ids, outcomes, strict=True):
        label = str(outcome).strip().lower()
        if label in _UP_LABELS:
            up_token_id = str(token_id)
        elif label in _DOWN_LABELS:
            down_token_id = str(token_id)
    if not up_token_id or not down_token_id:
        return None

    return PolymarketMarket(
        market_id=str(market_id),
        up_token_id=up_token_id,
        down_token_id=down_token_id,
        question=raw.get("question"),
    )


def _slug_for_window(symbol: str, window_start_ns: int, window_end_ns: int) -> str | None:
    if symbol != "BTCUSDT":
        return None
    duration_s = (window_end_ns - window_start_ns) // 1_000_000_000
    label = _SLUG_DURATION_LABELS.get(duration_s)
    if label is None:
        return None
    return f"btc-updown-{label}-{window_start_ns // 1_000_000_000}"


class GammaMarketDiscovery:
    """Discovers the Polymarket market and UP/DOWN tokens for a window.

    This adapter only issues GET requests through the injected client; it
    never places an order and never mutates a market.
    """

    def __init__(
        self,
        *,
        base_url: str = GAMMA_MARKETS_URL,
        client: RateLimitedRestClient | None = None,
        closed: bool = True,
    ) -> None:
        self._base_url = base_url
        self._client = client or RateLimitedRestClient()
        self._closed = closed

    def find_market_for_window(
        self,
        *,
        symbol: str,
        window_start_ns: int,
        window_end_ns: int,
    ) -> MarketLookupResult:
        slug = _slug_for_window(symbol, window_start_ns, window_end_ns)
        if slug is None:
            return MarketLookupResult(
                status="missing_market",
                detail="unsupported symbol or window duration for slug lookup",
            )
        try:
            payload = self._client.get(
                self._base_url,
                {"slug": slug, "closed": str(self._closed).lower()},
            )
        except PolymarketApiError as exc:
            return MarketLookupResult(status="api_error", detail=str(exc))

        if isinstance(payload, list):
            raw_markets: Any = payload
        elif isinstance(payload, dict):
            raw_markets = payload.get("data")
        else:
            raw_markets = None
        if not isinstance(raw_markets, list):
            return MarketLookupResult(status="api_error", detail="unexpected gamma response shape")

        matching = [raw for raw in raw_markets if isinstance(raw, dict) and raw.get("slug") == slug]
        if not matching:
            return MarketLookupResult(status="missing_market", detail=f"no market for slug {slug}")
        if len(matching) > 1:
            return MarketLookupResult(
                status="ambiguous_market", detail=f"{len(matching)} markets for slug {slug}"
            )

        raw = matching[0]
        end_ns = _parse_iso_ts_ns(raw.get("endDate"))
        if end_ns != window_end_ns:
            return MarketLookupResult(
                status="missing_market", detail="endDate does not match the expected window end"
            )

        market = _to_market(raw)
        if market is None:
            return MarketLookupResult(
                status="missing_token", detail="market is missing UP/DOWN token ids"
            )
        return MarketLookupResult(status="found", market=market)
