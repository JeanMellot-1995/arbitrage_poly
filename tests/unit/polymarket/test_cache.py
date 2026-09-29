from arbitrage_poly.polymarket.cache import FileJsonCache
from arbitrage_poly.polymarket.rest import PolymarketApiError


def test_cache_miss_without_network_raises_api_error(tmp_path):
    def _never_called(url, params):
        return {"never": "called"}

    cache = FileJsonCache(tmp_path / "cache.json", fetch_json=_never_called)

    try:
        cache("https://example.test", {"a": 1})
        raised = False
    except PolymarketApiError:
        raised = True
    assert raised


def test_cache_records_and_replays_offline(tmp_path):
    calls = {"count": 0}

    def _fetch(url, params):
        calls["count"] += 1
        return {"value": 42}

    cache_path = tmp_path / "cache.json"
    recording_cache = FileJsonCache(cache_path, fetch_json=_fetch, allow_network=True)

    first = recording_cache("https://example.test", {"a": 1})
    assert first == {"value": 42}
    assert calls["count"] == 1

    replay_cache = FileJsonCache(cache_path, fetch_json=_fetch, allow_network=False)
    second = replay_cache("https://example.test", {"a": 1})

    assert second == {"value": 42}
    assert calls["count"] == 1
