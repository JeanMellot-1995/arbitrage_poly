from datetime import UTC, datetime

from arbitrage_poly.polymarket.discovery import GammaMarketDiscovery
from arbitrage_poly.polymarket.rest import PolymarketApiError, RateLimitedRestClient

WINDOW_START_NS = 1_700_000_000_000_000_000
WINDOW_END_NS = WINDOW_START_NS + 300_000_000_000
EXPECTED_SLUG = f"btc-updown-5m-{WINDOW_START_NS // 1_000_000_000}"


def _iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _market(
    market_id="0xabc", up="up-token", down="down-token", slug=EXPECTED_SLUG, end_ns=WINDOW_END_NS
):
    return {
        "id": market_id,
        "slug": slug,
        "clobTokenIds": f'["{up}", "{down}"]',
        "outcomes": '["Up", "Down"]',
        "startDate": "2023-01-01T00:00:00.123456Z",
        "endDate": _iso(end_ns),
        "question": "Bitcoin Up or Down",
    }


def _discovery(fetch_json):
    client = RateLimitedRestClient(fetch_json=fetch_json, min_request_interval_s=0.0, max_retries=0)
    return GammaMarketDiscovery(client=client)


def test_finds_single_matching_market():
    def _fetch(url, params):
        return [_market()] if params.get("slug") == EXPECTED_SLUG else []

    discovery = _discovery(_fetch)

    result = discovery.find_market_for_window(
        symbol="BTCUSDT", window_start_ns=WINDOW_START_NS, window_end_ns=WINDOW_END_NS
    )

    assert result.status == "found"
    assert result.market.market_id == "0xabc"


def test_requests_closed_markets_since_windows_are_always_in_the_past():
    seen_params = {}

    def _fetch(url, params):
        seen_params.update(params)
        return [_market()]

    discovery = _discovery(_fetch)
    discovery.find_market_for_window(
        symbol="BTCUSDT", window_start_ns=WINDOW_START_NS, window_end_ns=WINDOW_END_NS
    )

    assert seen_params.get("closed") == "true"


def test_missing_market_when_slug_returns_nothing():
    discovery = _discovery(lambda url, params: [])

    result = discovery.find_market_for_window(
        symbol="BTCUSDT", window_start_ns=WINDOW_START_NS, window_end_ns=WINDOW_END_NS
    )

    assert result.status == "missing_market"


def test_missing_market_when_end_date_does_not_match_window_end():
    wrong_end = _market(end_ns=WINDOW_END_NS + 300_000_000_000)
    discovery = _discovery(lambda url, params: [wrong_end])

    result = discovery.find_market_for_window(
        symbol="BTCUSDT", window_start_ns=WINDOW_START_NS, window_end_ns=WINDOW_END_NS
    )

    assert result.status == "missing_market"


def test_ambiguous_market_when_slug_returns_two_entries():
    discovery = _discovery(lambda url, params: [_market(market_id="0x1"), _market(market_id="0x2")])

    result = discovery.find_market_for_window(
        symbol="BTCUSDT", window_start_ns=WINDOW_START_NS, window_end_ns=WINDOW_END_NS
    )

    assert result.status == "ambiguous_market"


def test_missing_token_when_outcomes_are_not_up_down():
    raw = _market()
    raw["outcomes"] = '["Team A", "Team B"]'
    discovery = _discovery(lambda url, params: [raw])

    result = discovery.find_market_for_window(
        symbol="BTCUSDT", window_start_ns=WINDOW_START_NS, window_end_ns=WINDOW_END_NS
    )

    assert result.status == "missing_token"


def test_api_error_is_surfaced_without_raising():
    def _failing_fetch(url, params):
        raise PolymarketApiError("boom")

    discovery = _discovery(_failing_fetch)

    result = discovery.find_market_for_window(
        symbol="BTCUSDT", window_start_ns=WINDOW_START_NS, window_end_ns=WINDOW_END_NS
    )

    assert result.status == "api_error"
    assert "boom" in result.detail


def test_unsupported_symbol_is_reported_as_missing_market():
    discovery = _discovery(lambda url, params: [_market()])

    result = discovery.find_market_for_window(
        symbol="ETHUSDT", window_start_ns=WINDOW_START_NS, window_end_ns=WINDOW_END_NS
    )

    assert result.status == "missing_market"

