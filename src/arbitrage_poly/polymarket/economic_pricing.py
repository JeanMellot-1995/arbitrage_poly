"""Combine Gamma discovery and CLOB historical prices at a decision offset.

A missing market, token or price only removes the window from the economic
simulation (edge, sizing, P/L); it never invalidates the Oracle's own score.
"""

from __future__ import annotations

from arbitrage_poly.polymarket.discovery import GammaMarketDiscovery
from arbitrage_poly.polymarket.historical_prices import ClobHistoricalPriceClient
from arbitrage_poly.polymarket.models import EconomicWindowPricing

DEFAULT_PREDICTION_OFFSET_NS = 120 * 1_000_000_000
_STATUS_PRIORITY = ("api_error", "missing_price", "stale_price")


def _combine_status(up_status: str, down_status: str) -> str:
    for status in _STATUS_PRIORITY:
        if status in (up_status, down_status):
            return status
    return "priced"


def price_window_at_offset(
    *,
    window_start_ns: int,
    window_end_ns: int,
    symbol: str,
    discovery: GammaMarketDiscovery,
    price_client: ClobHistoricalPriceClient,
    prediction_offset_ns: int = DEFAULT_PREDICTION_OFFSET_NS,
) -> EconomicWindowPricing:
    """Resolve UP/DOWN Polymarket prices for one window at `window_end - offset`."""

    decision_ts_ns = window_end_ns - prediction_offset_ns
    lookup = discovery.find_market_for_window(
        symbol=symbol, window_start_ns=window_start_ns, window_end_ns=window_end_ns
    )
    if lookup.status != "found" or lookup.market is None:
        return EconomicWindowPricing(
            window_start_ns=window_start_ns,
            decision_ts_ns=decision_ts_ns,
            status=lookup.status,
        )

    market = lookup.market
    up_lookup = price_client.get_price_at_or_before(market.up_token_id, decision_ts_ns)
    down_lookup = price_client.get_price_at_or_before(market.down_token_id, decision_ts_ns)
    status = _combine_status(up_lookup.status, down_lookup.status)
    source = (up_lookup.price_point or down_lookup.price_point)
    return EconomicWindowPricing(
        window_start_ns=window_start_ns,
        decision_ts_ns=decision_ts_ns,
        status=status,
        market_id=market.market_id,
        up_token_id=market.up_token_id,
        down_token_id=market.down_token_id,
        up_price=up_lookup.price_point.price if up_lookup.price_point else None,
        down_price=down_lookup.price_point.price if down_lookup.price_point else None,
        up_price_ts_ns=up_lookup.price_point.ts_ns if up_lookup.price_point else None,
        down_price_ts_ns=down_lookup.price_point.ts_ns if down_lookup.price_point else None,
        up_price_age_ns=up_lookup.age_ns,
        down_price_age_ns=down_lookup.age_ns,
        price_source=source.source if source else None,
    )
