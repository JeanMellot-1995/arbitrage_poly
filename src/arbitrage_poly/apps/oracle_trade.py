"""Live Oracle trader: Binance -> Oracle decision at T-120s -> Polymarket order.

For each five-minute BTC UP/DOWN window, the Oracle is fed live Binance data
and its prediction is frozen ``--prediction-offset-s`` seconds (default 120)
before the window closes -- the same convention as ``live_decisions`` and the
backtest. The favoured side (UP if ``prob_up >= 0.5``, else DOWN) is then
bought on Polymarket:

- if the Oracle fair value of that side is **below** the best ask (the share
  is too expensive), a passive GTC limit BUY is posted at the fair value
  (floored to the 0.01 tick);
- otherwise (fair value >= best ask), a market order is executed: a GTC limit
  BUY that crosses the spread (best ask + one tick), the same "market-like"
  execution as ``arbitrage_poly.apps.market_order``.

Dry run by default (orders are only logged and recorded); ``--yes`` sends real
orders with the credentials of ``--creds-file``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arbitrage_poly.apps.backtest import build_oracle
from arbitrage_poly.apps.binance_windows import fetch_klines_1s
from arbitrage_poly.apps.live_decisions import (
    WINDOW_NS,
    _parse_utc_ns,
    run_live_decisions,
    warm_up_oracle,
)
from arbitrage_poly.apps.market_order import _best_ask
from arbitrage_poly.oracle.volatility import DEFAULT_WARMUP_S, HORIZON_EWMA, VOLATILITY_MODELS
from arbitrage_poly.polymarket.discovery import GammaMarketDiscovery
from arbitrage_poly.polymarket.rest import RateLimitedRestClient
from arbitrage_poly.price_collection.binance_ws import BinancePriceReader

LOGGER = logging.getLogger(__name__)

TICK_SIZE = 0.01
MIN_PRICE = 0.01
MAX_PRICE = 0.99
MIN_ORDER_SHARES = 5.0
# Cross one tick above the best ask so the "market" order fills immediately.
CROSSING_BUFFER = 0.01

LIMIT = "LIMIT"
MARKET = "MARKET"

CSV_FIELDS = [
    "window_start_utc",
    "window_end_utc",
    "decision_ts_utc",
    "prob_up",
    "side",
    "fair_value",
    "token_id",
    "best_ask",
    "order_kind",
    "order_price",
    "shares",
    "stake_usd",
    "status",
    "detail",
]


@dataclass(frozen=True, slots=True)
class OrderPlan:
    kind: str
    price: float
    shares: float


def _floor_tick(value: float) -> float:
    return round(math.floor(value / TICK_SIZE + 1e-9) * TICK_SIZE, 2)


def plan_order(
    fair_value: float,
    best_ask: float | None,
    *,
    stake_usd: float,
) -> OrderPlan | None:
    """Apply the execution rule; ``None`` when no valid order can be placed.

    fair value < best ask (or no ask) -> LIMIT BUY at the fair value (floored to tick).
    fair value >= best ask            -> MARKET BUY (best ask + one tick, capped at 0.99).
    """

    if best_ask is not None and fair_value >= best_ask:
        kind = MARKET
        price = round(min(best_ask + CROSSING_BUFFER, MAX_PRICE), 2)
    else:
        kind = LIMIT
        price = min(_floor_tick(fair_value), MAX_PRICE)
    if price < MIN_PRICE:
        return None
    shares = round(max(stake_usd / price, MIN_ORDER_SHARES), 2)
    return OrderPlan(kind=kind, price=price, shares=shares)


OrderSender = Callable[..., Any]
AskFetcher = Callable[[str], float | None]
MarketFinder = Callable[[int], tuple[str, str] | None]


def execute_decision(
    decision: dict[str, object],
    *,
    find_tokens: MarketFinder,
    fetch_best_ask: AskFetcher,
    stake_usd: float,
    send_order: OrderSender | None,
) -> dict[str, object]:
    """Turn one frozen Oracle decision into a Polymarket order (or a dry-run record)."""

    side = str(decision["side"])
    prob_up = float(decision["prob_up"])  # type: ignore[arg-type]
    fair_value = prob_up if side == "UP" else 1.0 - prob_up
    row: dict[str, object] = {
        "window_start_utc": decision["window_start_utc"],
        "window_end_utc": decision["window_end_utc"],
        "decision_ts_utc": decision["decision_ts_utc"],
        "prob_up": prob_up,
        "side": side,
        "fair_value": round(fair_value, 6),
    }

    tokens = find_tokens(_parse_utc_ns(str(decision["window_start_utc"])))
    if tokens is None:
        return {**row, "status": "skipped", "detail": "no_open_market"}
    token_id = tokens[0] if side == "UP" else tokens[1]
    best_ask = fetch_best_ask(token_id)
    plan = plan_order(fair_value, best_ask, stake_usd=stake_usd)
    row.update(token_id=token_id, best_ask=best_ask)
    if plan is None:
        return {**row, "status": "skipped", "detail": "fair_value_below_min_price"}
    row.update(
        order_kind=plan.kind,
        order_price=plan.price,
        shares=plan.shares,
        stake_usd=round(plan.price * plan.shares, 4),
    )
    LOGGER.info("oracle_order_plan %s", row)

    if send_order is None:
        return {**row, "status": "dry_run", "detail": ""}
    try:
        # Always BUY: the UP/DOWN token itself encodes the directional bet.
        response = send_order(
            token_id=token_id,
            price=plan.price,
            quantity=plan.shares,
            side="BUY",
            order_type="GTC",
        )
    except Exception as exc:  # noqa: BLE001 - one failed order must not stop the loop
        LOGGER.error("oracle_order_failed %s error=%s", row, exc, exc_info=True)
        return {**row, "status": "error", "detail": str(exc)}
    return {**row, "status": "sent", "detail": str(response)}


def _gamma_token_finder(client: RateLimitedRestClient) -> MarketFinder:
    discovery = GammaMarketDiscovery(client=client, closed=False)

    def find_tokens(window_start_ns: int) -> tuple[str, str] | None:
        lookup = discovery.find_market_for_window(
            symbol="BTCUSDT",
            window_start_ns=window_start_ns,
            window_end_ns=window_start_ns + WINDOW_NS,
        )
        if lookup.market is None:
            LOGGER.warning(
                "oracle_trade_no_market status=%s detail=%s", lookup.status, lookup.detail
            )
            return None
        return lookup.market.up_token_id, lookup.market.down_token_id

    return find_tokens


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Trade the Oracle decision taken at T-offset: LIMIT BUY at fair value when the "
            "share is more expensive than the fair value, MARKET BUY otherwise."
        )
    )
    parser.add_argument("--output", type=Path, required=True, help="CSV log of orders")
    parser.add_argument("--prediction-offset-s", type=float, default=120.0)
    parser.add_argument("--stake-usd", type=float, default=5.0, help="USDC per order")
    parser.add_argument(
        "--max-late-s",
        type=float,
        default=5.0,
        help="Skip a window if the decision would be taken this many seconds after the cutoff",
    )
    parser.add_argument("--volatility-model", choices=VOLATILITY_MODELS, default=HORIZON_EWMA)
    parser.add_argument("--creds-file", type=Path, default=Path("creds.json"))
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Send real orders (real money). Without it, orders are only logged (dry run).",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> None:
    offset_ns = int(args.prediction_offset_s * 1_000_000_000)
    if offset_ns <= 0 or offset_ns >= WINDOW_NS:
        raise ValueError("prediction offset must be positive and shorter than five minutes")
    if args.stake_usd <= 0:
        raise ValueError("stake must be positive")

    send_order: OrderSender | None = None
    if args.yes:
        from arbitrage_poly.polymarket.execution import PolymarketExecutor

        LOGGER.warning("oracle_trade_live_orders_enabled creds_file=%s", args.creds_file)
        send_order = PolymarketExecutor(api_file_path=args.creds_file).place_order
    else:
        LOGGER.info("oracle_trade_dry_run (pass --yes to send real orders)")

    rest_client = RateLimitedRestClient()
    find_tokens = _gamma_token_finder(rest_client)

    def fetch_best_ask(token_id: str) -> float | None:
        return _best_ask(rest_client, token_id)

    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output_path.exists() or output_path.stat().st_size == 0
    pending: set[asyncio.Task[None]] = set()

    with output_path.open("a", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
            output_file.flush()

        async def handle(decision: dict[str, object]) -> None:
            row = await asyncio.to_thread(
                execute_decision,
                decision,
                find_tokens=find_tokens,
                fetch_best_ask=fetch_best_ask,
                stake_usd=args.stake_usd,
                send_order=send_order,
            )
            writer.writerow(row)
            output_file.flush()
            LOGGER.info("oracle_trade %s", row)

        def on_decision(decision: dict[str, object]) -> None:
            LOGGER.info("live_decision %s", decision)
            # REST calls run off the event loop so Binance ticks keep flowing.
            task = asyncio.get_running_loop().create_task(handle(decision))
            pending.add(task)
            task.add_done_callback(pending.discard)

        oracle = build_oracle(args.volatility_model)
        if args.volatility_model == HORIZON_EWMA:
            now_ms = int(time.time() * 1000)
            warmup_ms = int((DEFAULT_WARMUP_S + 60) * 1000)
            count = warm_up_oracle(oracle, fetch_klines_1s("BTCUSDT", now_ms - warmup_ms, now_ms))
            LOGGER.info("volatility_warmup klines=%d ready=%s", count, oracle.volatility_ready)
        try:
            await run_live_decisions(
                reader=BinancePriceReader(),
                oracle=oracle,
                offset_ns=offset_ns,
                on_decision=on_decision,
                max_late_ns=int(args.max_late_s * 1_000_000_000),
            )
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        LOGGER.info("oracle_trade_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
