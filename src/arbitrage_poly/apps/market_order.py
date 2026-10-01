"""CLI: place a marketable BUY order on the currently open 5-minute BTC UP/DOWN window.

Discovers the live Polymarket market for the current 5-minute window, reads
the top of book, and submits (or dry-runs) an aggressive GTC limit order that
crosses the spread -- the same "market-like" execution already used by the
automated live-trading loop (``arbitrage_poly.apps.live``). A true FOK market
order is deliberately avoided here: py_clob_client_v2's own market-order
amount rounding can produce an implied price that violates the CLOB's tick
size and gets rejected with a 400 error.
Defaults to a dry run (no network call, no funds at risk); pass ``--yes`` to
actually submit the order.
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from arbitrage_poly.oracle.reference import DEFAULT_WINDOW_NS
from arbitrage_poly.oracle.reference import window_start_ns as compute_window_start_ns
from arbitrage_poly.polymarket.discovery import GammaMarketDiscovery
from arbitrage_poly.polymarket.execution import PolymarketExecutor
from arbitrage_poly.polymarket.live_book import CLOB_BOOK_URL
from arbitrage_poly.polymarket.rest import PolymarketApiError, RateLimitedRestClient

LOGGER = logging.getLogger(__name__)

MIN_ORDER_SHARES = 5.0
TICK_SIZE = 0.01
# Cross a tick above the best ask so the limit order is marketable immediately.
CROSSING_BUFFER = 0.01


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Place a marketable BUY order on the currently open 5-minute BTC UP/DOWN window"
    )
    parser.add_argument("--side", choices=["UP", "DOWN"], required=True)
    parser.add_argument(
        "--quantity", type=float, required=True, help="Order size (USDC amount to spend)"
    )
    parser.add_argument(
        "--creds-file",
        type=Path,
        default=Path("creds.json"),
        help="Path to the API credentials file (default: ./creds.json)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually submit the order. Without this flag, only a dry run is printed.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def _best_ask(client: RateLimitedRestClient, token_id: str) -> float | None:
    try:
        payload = client.get(CLOB_BOOK_URL, {"token_id": token_id})
    except PolymarketApiError:
        return None
    asks = payload.get("asks") if isinstance(payload, dict) else None
    if not asks:
        return None
    prices = [
        float(level["price"]) for level in asks if isinstance(level, dict) and "price" in level
    ]
    return min(prices) if prices else None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    window_start = compute_window_start_ns(time.time_ns(), DEFAULT_WINDOW_NS)
    window_end = window_start + DEFAULT_WINDOW_NS

    rest_client = RateLimitedRestClient()
    discovery = GammaMarketDiscovery(client=rest_client, closed=False)
    lookup = discovery.find_market_for_window(
        symbol="BTCUSDT", window_start_ns=window_start, window_end_ns=window_end
    )
    if lookup.market is None:
        print(
            f"No open BTC 5-minute market found for the current window "
            f"(status={lookup.status}, detail={lookup.detail})"
        )
        return 1

    token_id = lookup.market.up_token_id if args.side == "UP" else lookup.market.down_token_id

    best_ask = _best_ask(rest_client, token_id)
    if best_ask is None:
        print(f"No ask liquidity available for token_id={token_id}")
        return 1

    price = round(min(best_ask + CROSSING_BUFFER, 1 - TICK_SIZE), 2)
    qty_shares = round(max(args.quantity / price, MIN_ORDER_SHARES), 2)

    print(
        f"Market order: side=BUY outcome={args.side} token_id={token_id} "
        f"best_ask={best_ask} price={price} shares={qty_shares} stake_usd={args.quantity} "
        f"window_start_ns={window_start} question={lookup.market.question!r}"
    )

    if not args.yes:
        print("Dry run only (no order sent). Re-run with --yes to submit for real.")
        return 0

    executor = PolymarketExecutor(api_file_path=args.creds_file)
    # Always BUY: the UP/DOWN token itself encodes the directional bet.
    response = executor.place_order(
        token_id=token_id,
        price=price,
        quantity=qty_shares,
        side="BUY",
        order_type="GTC",
    )
    print(f"Response: {response}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
