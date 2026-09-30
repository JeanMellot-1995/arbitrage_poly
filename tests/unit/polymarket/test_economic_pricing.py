from datetime import UTC, datetime

from arbitrage_poly.polymarket.discovery import GammaMarketDiscovery
from arbitrage_poly.polymarket.economic_pricing import price_window_at_offset
from arbitrage_poly.polymarket.historical_prices import ClobHistoricalPriceClient
from arbitrage_poly.polymarket.rest import RateLimitedRestClient

WINDOW_START_NS = 1_700_000_000_000_000_000
WINDOW_END_NS = WINDOW_START_NS + 300_000_000_000
DECISION_TS_NS = WINDOW_END_NS - 120_000_000_000
EXPECTED_SLUG = f"btc-updown-5m-{WINDOW_START_NS // 1_000_000_000}"


def _rest_client(fetch_json):
    return RateLimitedRestClient(fetch_json=fetch_json, min_request_interval_s=0.0, max_retries=0)


def _market_payload():
    end_dt = datetime.fromtimestamp(WINDOW_END_NS / 1_000_000_000, tz=UTC)
    end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        {
            "id": "0xabc",
            "slug": EXPECTED_SLUG,
            "clobTokenIds": '["up-token", "down-token"]',
            "outcomes": '["Up", "Down"]',
            "startDate": "2023-01-01T00:00:00.123456Z",
            "endDate": end_iso,
        }
    ]


def test_priced_window_returns_both_up_and_down_prices():
    discovery = GammaMarketDiscovery(client=_rest_client(lambda url, params: _market_payload()))

    def _prices(url, params):
        ts = (DECISION_TS_NS - 5_000_000_000) // 1_000_000_000
        price = 0.55 if params["market"] == "up-token" else 0.45
        return {"history": [{"t": ts, "p": price}]}

    price_client = ClobHistoricalPriceClient(client=_rest_client(_prices))

    result = price_window_at_offset(
        window_start_ns=WINDOW_START_NS,
        window_end_ns=WINDOW_END_NS,
        symbol="BTCUSDT",
        discovery=discovery,
        price_client=price_client,
    )

    assert result.status == "priced"
    assert result.market_id == "0xabc"
    assert result.up_price == 0.55
    assert result.down_price == 0.45
    assert result.decision_ts_ns == DECISION_TS_NS


def test_missing_market_short_circuits_before_pricing():
    discovery = GammaMarketDiscovery(client=_rest_client(lambda url, params: []))
    empty_prices = _rest_client(lambda url, params: {"history": []})
    price_client = ClobHistoricalPriceClient(client=empty_prices)

    result = price_window_at_offset(
        window_start_ns=WINDOW_START_NS,
        window_end_ns=WINDOW_END_NS,
        symbol="BTCUSDT",
        discovery=discovery,
        price_client=price_client,
    )

    assert result.status == "missing_market"
    assert result.market_id is None
    assert result.up_price is None


def test_missing_price_on_one_side_excludes_the_window():
    discovery = GammaMarketDiscovery(client=_rest_client(lambda url, params: _market_payload()))

    def _prices(url, params):
        if params["market"] == "up-token":
            return {"history": []}
        ts = (DECISION_TS_NS - 5_000_000_000) // 1_000_000_000
        return {"history": [{"t": ts, "p": 0.45}]}

    price_client = ClobHistoricalPriceClient(client=_rest_client(_prices))

    result = price_window_at_offset(
        window_start_ns=WINDOW_START_NS,
        window_end_ns=WINDOW_END_NS,
        symbol="BTCUSDT",
        discovery=discovery,
        price_client=price_client,
    )

    assert result.status == "missing_price"
    assert result.up_price is None
    assert result.down_price == 0.45
