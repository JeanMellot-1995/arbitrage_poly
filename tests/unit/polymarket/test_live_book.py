from arbitrage_poly.models import FairValue
from arbitrage_poly.polymarket.live_book import PolymarketLiveBook
from arbitrage_poly.polymarket.models import MarketLookupResult, PolymarketMarket


class _Discovery:
    def __init__(self):
        self.calls = 0

    def find_market_for_window(self, **kwargs):
        self.calls += 1
        return MarketLookupResult(
            status="found",
            market=PolymarketMarket("market", "up-token", "down-token"),
        )


def test_snapshot_cache_avoids_requesting_books_on_every_tick():
    calls = []
    monotonic_values = iter((10.0, 10.0, 10.5))

    def fetch(url, params):
        calls.append(params["token_id"])
        return {
            "asks": [{"price": "0.40", "size": "10"}],
            "bids": [{"price": "0.39", "size": "10"}],
        }

    class _Client:
        def get(self, url, params):
            return fetch(url, params)

    fair_value = FairValue(
        ts_ns=1_700_000_000_100_000_000,
        window_start_ns=1_700_000_000_000_000_000,
        reference_price=100.0,
        current_price=101.0,
        prob_up=0.6,
        prob_down=0.4,
        model="test",
        volatility=0.1,
        remaining_ns=100_000_000_000,
    )
    discovery = _Discovery()
    provider = PolymarketLiveBook(
        discovery=discovery,
        client=_Client(),
        refresh_interval_s=1.0,
        monotonic=lambda: next(monotonic_values),
    )

    first = provider.snapshot(fair_value)
    second = provider.snapshot(fair_value)

    assert first is second
    assert discovery.calls == 1
    assert calls == ["up-token", "down-token"]
