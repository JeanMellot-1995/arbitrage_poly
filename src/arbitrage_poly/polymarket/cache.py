"""Read-through JSON cache for Polymarket REST responses.

Enables deterministic offline replay: once responses are recorded to a local
file, later runs reuse them without any network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arbitrage_poly.polymarket.rest import JsonFetcher, PolymarketApiError, default_json_fetcher


def _cache_key(url: str, params: dict[str, Any]) -> str:
    encoded_params = json.dumps(params, sort_keys=True, default=str)
    return f"{url}?{encoded_params}"


class FileJsonCache:
    """A `JsonFetcher` backed by a local JSON file of recorded responses.

    By default it never touches the network: a cache miss raises
    `PolymarketApiError`, which callers turn into an explicit `api_error`
    status instead of a silent or crashing failure. Set `allow_network=True`
    to record live responses into the file on a cache miss.
    """

    def __init__(
        self,
        path: Path,
        *,
        fetch_json: JsonFetcher = default_json_fetcher,
        allow_network: bool = False,
    ) -> None:
        self._path = path
        self._fetch_json = fetch_json
        self._allow_network = allow_network
        self._entries: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._entries, indent=2, sort_keys=True), encoding="utf-8")

    def __call__(self, url: str, params: dict[str, Any]) -> Any:
        key = _cache_key(url, params)
        if key in self._entries:
            return self._entries[key]
        if not self._allow_network:
            raise PolymarketApiError(f"no cached response for {key} and network access is disabled")
        payload = self._fetch_json(url, params)
        self._entries[key] = payload
        self._save()
        return payload
