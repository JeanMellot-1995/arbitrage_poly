"""Shared read-only REST helpers: JSON GET requests and rate limiting.

This module never issues writes. It is the single place where Polymarket
HTTP access, retries and rate limiting are implemented so that `discovery.py`
and `historical_prices.py` share the same resilience policy.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

JsonFetcher = Callable[[str, dict[str, Any]], Any]


class PolymarketApiError(RuntimeError):
    """Raised when a Polymarket REST request fails or returns invalid JSON."""


def default_json_fetcher(url: str, params: dict[str, Any], *, timeout_s: float = 10.0) -> Any:
    """Perform a read-only GET request and parse the JSON body."""

    query = urlencode(params, doseq=True)
    full_url = f"{url}?{query}" if query else url
    try:
        with urlopen(full_url, timeout=timeout_s) as response:  # noqa: S310 - read-only GET
            payload = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise PolymarketApiError(f"request failed: {exc}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PolymarketApiError("response is not valid JSON") from exc


class RateLimitedRestClient:
    """Enforces a minimum interval between requests and bounded retries."""

    def __init__(
        self,
        *,
        fetch_json: JsonFetcher = default_json_fetcher,
        min_request_interval_s: float = 0.2,
        max_retries: int = 2,
        retry_backoff_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._fetch_json = fetch_json
        self._min_request_interval_s = min_request_interval_s
        self._max_retries = max_retries
        self._retry_backoff_s = retry_backoff_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def get(self, url: str, params: dict[str, Any]) -> Any:
        last_error: PolymarketApiError | None = None
        for attempt in range(self._max_retries + 1):
            self._throttle()
            self._last_request_at = self._monotonic()
            try:
                return self._fetch_json(url, params)
            except PolymarketApiError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    self._sleep(self._retry_backoff_s * (attempt + 1))
        assert last_error is not None  # loop always sets it before exhausting retries
        raise last_error

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        wait_s = self._min_request_interval_s - elapsed
        if wait_s > 0:
            self._sleep(wait_s)
