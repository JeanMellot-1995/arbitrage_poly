"""Command-line entry point for the live paper/execution loop."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from arbitrage_poly.apps.live import LiveDecision, run_live
from arbitrage_poly.oracle.oracle import PriceOracle
from arbitrage_poly.polymarket.discovery import GammaMarketDiscovery
from arbitrage_poly.polymarket.execution import PolymarketExecutor
from arbitrage_poly.polymarket.live_book import PolymarketLiveBook
from arbitrage_poly.polymarket.rest import RateLimitedRestClient
from arbitrage_poly.price_collection.binance_ws import BinancePriceReader
from arbitrage_poly.pricing import PricingConfig, SizingConfig

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Polymarket paper/execution loop")
    parser.add_argument("--quantity", type=float, default=1.0)
    parser.add_argument("--min-net-edge", type=float, default=0.01)
    parser.add_argument("--slippage-buffer", type=float, default=0.0)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--fixed-stake-usd", type=float, default=10.0)
    parser.add_argument("--max-stake-usd", type=float, default=20.0)
    parser.add_argument("--refresh-interval-s", type=float, default=1.0)
    parser.add_argument("--request-interval-s", type=float, default=0.2)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--prediction-offset-s",
        type=float,
        default=120.0,
        help=(
            "Freeze at most one decision per window, taken this many seconds before it "
            "closes (default T-120s, 0 disables; same convention as "
            "arbitrage-poly-live-decisions)."
        ),
    )
    parser.add_argument(
        "--enable-live-trading",
        action="store_true",
        help=(
            "DANGER: place real orders on Polymarket for accepted opportunities. "
            "Off by default (paper mode)."
        ),
    )
    parser.add_argument(
        "--creds-file",
        type=Path,
        default=Path("creds.json"),
        help="API credentials file, only used with --enable-live-trading (default: ./creds.json)",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


async def _log_decision(decision: LiveDecision) -> None:
    opportunity = decision.opportunity
    payload = {
        "ts_ns": decision.tick.ts_ns,
        "prob_up": decision.fair_value.prob_up,
        "accepted": opportunity.accepted,
        "side": opportunity.side,
        "reason": opportunity.reason,
        "limit_price": opportunity.edge.limit_price if opportunity.edge else None,
        "net_edge": opportunity.net_edge,
        "stake_usd": decision.stake_usd,
    }
    LOGGER.log(logging.INFO if opportunity.accepted else logging.DEBUG, json.dumps(payload))


async def _run(args: argparse.Namespace) -> None:
    refresh_interval_ns = int(args.refresh_interval_s * 1_000_000_000)
    reader = BinancePriceReader()
    client = RateLimitedRestClient(
        min_request_interval_s=args.request_interval_s,
        max_retries=args.max_retries,
    )
    provider = PolymarketLiveBook(
        discovery=GammaMarketDiscovery(client=client, closed=False),
        client=client,
        refresh_interval_s=args.refresh_interval_s,
    )
    executor = None
    if args.enable_live_trading:
        LOGGER.warning("live_trading_enabled", extra={"creds_file": str(args.creds_file)})
        executor = PolymarketExecutor(api_file_path=args.creds_file)
    await run_live(
        reader=reader,
        oracle=PriceOracle(),
        snapshot_provider=provider,
        pricing_config=PricingConfig(
            min_net_edge=args.min_net_edge,
            slippage_buffer=args.slippage_buffer,
            tick_size=args.tick_size,
            max_snapshot_age_ns=max(refresh_interval_ns * 2, 1_000_000_000),
        ),
        sizing_config=SizingConfig(
            mode="fixed",
            fixed_stake_usd=args.fixed_stake_usd,
            max_stake_per_market=args.max_stake_usd,
            tick_size=args.tick_size,
        ),
        quantity=args.quantity,
        on_decision=_log_decision,
        executor=executor,
        offset_ns=int(args.prediction_offset_s * 1_000_000_000),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        LOGGER.info("live_loop_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
