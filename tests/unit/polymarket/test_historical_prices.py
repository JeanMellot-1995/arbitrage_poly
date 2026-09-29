from arbitrage_poly.polymarket.historical_prices import ClobHistoricalPriceClient
from arbitrage_poly.polymarket.rest import PolymarketApiError, RateLimitedRestClient

DECISION_TS_NS = 1_700_000_000_000_000_000


def _client(fetch_json, **kwargs):
    rest_client = RateLimitedRestClient(
        fetch_json=fetch_json, min_request_interval_s=0.0, max_retries=0
    )
    return ClobHistoricalPriceClient(client=rest_client, **kwargs)


def test_selects_last_point_at_or_before_decision_timestamp():
    def _fetch(url, params):
        return {
            "history": [
                {"t": (DECISION_TS_NS - 30_000_000_000) // 1_000_000_000, "p": 0.40},
                {"t": (DECISION_TS_NS - 10_000_000_000) // 1_000_000_000, "p": 0.42},
                {"t": (DECISION_TS_NS + 10_000_000_000) // 1_000_000_000, "p": 0.99},
            ]
        }

    client = _client(_fetch)

    result = client.get_price_at_or_before("token-up", DECISION_TS_NS)

    assert result.status == "priced"
    assert result.price_point.price == 0.42
    assert result.price_point.ts_ns == DECISION_TS_NS - 10_000_000_000
    assert result.age_ns == 10_000_000_000


def test_never_uses_a_point_after_the_decision_timestamp():
    def _fetch(url, params):
        return {"history": [{"t": DECISION_TS_NS // 1_000_000_000 + 1, "p": 0.77}]}

    client = _client(_fetch)

    result = client.get_price_at_or_before("token-up", DECISION_TS_NS)

    assert result.status == "missing_price"


def test_missing_price_when_history_is_empty():
    client = _client(lambda url, params: {"history": []})

    result = client.get_price_at_or_before("token-up", DECISION_TS_NS)

    assert result.status == "missing_price"


def test_stale_price_beyond_max_age():
    def _fetch(url, params):
        return {"history": [{"t": (DECISION_TS_NS - 180_000_000_000) // 1_000_000_000, "p": 0.5}]}

    client = _client(_fetch)

    result = client.get_price_at_or_before(
        "token-up", DECISION_TS_NS, max_age_ns=120_000_000_000, lookback_ns=600_000_000_000
    )

    assert result.status == "stale_price"
    assert result.price_point.price == 0.5


def test_api_error_is_returned_as_status():
    def _failing_fetch(url, params):
        raise PolymarketApiError("clob down")

    client = _client(_failing_fetch)

    result = client.get_price_at_or_before("token-up", DECISION_TS_NS)

    assert result.status == "api_error"


def test_paginates_until_next_cursor_is_absent():
    pages = [
        {
            "history": [{"t": (DECISION_TS_NS - 20_000_000_000) // 1_000_000_000, "p": 0.3}],
            "next_cursor": "page-2",
        },
        {"history": [{"t": (DECISION_TS_NS - 5_000_000_000) // 1_000_000_000, "p": 0.6}]},
    ]
    calls = {"count": 0}

    def _fetch(url, params):
        page = pages[calls["count"]]
        calls["count"] += 1
        return page

    client = _client(_fetch)

    result = client.get_price_at_or_before("token-up", DECISION_TS_NS)

    assert calls["count"] == 2
    assert result.status == "priced"
    assert result.price_point.price == 0.6
