import pytest

from arbitrage_poly.polymarket.rest import PolymarketApiError, RateLimitedRestClient


def test_retries_bounded_number_of_times_then_raises():
    calls = {"count": 0}

    def _always_fails(url, params):
        calls["count"] += 1
        raise PolymarketApiError("transient")

    client = RateLimitedRestClient(
        fetch_json=_always_fails,
        min_request_interval_s=0.0,
        max_retries=2,
        retry_backoff_s=0.0,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(PolymarketApiError):
        client.get("https://example.test", {})

    assert calls["count"] == 3


def test_succeeds_after_transient_failures():
    calls = {"count": 0}

    def _fails_twice_then_succeeds(url, params):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PolymarketApiError("transient")
        return {"ok": True}

    client = RateLimitedRestClient(
        fetch_json=_fails_twice_then_succeeds,
        min_request_interval_s=0.0,
        max_retries=2,
        retry_backoff_s=0.0,
        sleep=lambda _seconds: None,
    )

    result = client.get("https://example.test", {})

    assert result == {"ok": True}
    assert calls["count"] == 3


def test_enforces_minimum_interval_between_requests():
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def _monotonic() -> float:
        return clock["now"]

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    client = RateLimitedRestClient(
        fetch_json=lambda url, params: {"ok": True},
        min_request_interval_s=1.0,
        max_retries=0,
        sleep=_sleep,
        monotonic=_monotonic,
    )

    client.get("https://example.test", {})
    clock["now"] += 0.2
    client.get("https://example.test", {})

    assert sleeps == [pytest.approx(0.8)]
