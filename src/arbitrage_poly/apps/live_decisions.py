"""Live, Binance-only Oracle decision recorder.

Connects to the public Binance market-data WebSocket (no Polymarket access
required) and, for each five-minute BTC up/down window, freezes and records
the Oracle prediction at a fixed offset before the window closes -- the same
``prediction_offset_s`` convention used by ``arbitrage_poly.apps.backtest``.
Outcomes are not fetched automatically; they are meant to be checked by hand
against Polymarket afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from arbitrage_poly.apps.backtest import AGG_TRADE_SOURCE, build_oracle
from arbitrage_poly.apps.binance_windows import Kline, fetch_klines_1s, kline_ticks
from arbitrage_poly.models import FairValue
from arbitrage_poly.oracle.oracle import PriceOracle
from arbitrage_poly.oracle.reference import DEFAULT_WINDOW_NS
from arbitrage_poly.oracle.volatility import DEFAULT_WARMUP_S, HORIZON_EWMA, VOLATILITY_MODELS
from arbitrage_poly.price_collection.binance_ws import BinancePriceReader

LOGGER = logging.getLogger(__name__)

WINDOW_NS = DEFAULT_WINDOW_NS
PREDICTION_OFFSET_NS = 60 * 1_000_000_000

CSV_FIELDS = [
    "window_start_utc",
    "window_end_utc",
    "prediction_cutoff_utc",
    "decision_ts_utc",
    "reference_price",
    "current_price",
    "prob_up",
    "prob_down",
    "side",
    "volatility",
    "model",
]


def _format_ns(ts_ns: int) -> str:
    return (
        datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_utc_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1_000_000_000)


def _last_window_start_ns(output_path: Path) -> int | None:
    """Resume support: skip re-deciding the window already recorded last run."""

    if not output_path.exists() or output_path.stat().st_size == 0:
        return None
    with output_path.open(newline="", encoding="utf-8") as existing_file:
        rows = list(csv.DictReader(existing_file))
    if not rows:
        return None
    return _parse_utc_ns(rows[-1]["window_start_utc"])


def _decision_row(fair_value: FairValue, *, offset_ns: int) -> dict[str, object]:
    window_end_ns = fair_value.window_start_ns + WINDOW_NS
    side = "UP" if fair_value.prob_up >= 0.5 else "DOWN"
    return {
        "window_start_utc": _format_ns(fair_value.window_start_ns),
        "window_end_utc": _format_ns(window_end_ns),
        "prediction_cutoff_utc": _format_ns(window_end_ns - offset_ns),
        "decision_ts_utc": _format_ns(fair_value.ts_ns),
        "reference_price": fair_value.reference_price,
        "current_price": fair_value.current_price,
        "prob_up": fair_value.prob_up,
        "prob_down": fair_value.prob_down,
        "side": side,
        "volatility": fair_value.volatility,
        "model": fair_value.model,
    }


def warm_up_oracle(oracle: PriceOracle, klines: Iterable[Kline]) -> int:
    """Feed historical 1s klines as agg-trade ticks; also sets the current window reference."""

    count = 0
    for kline in klines:
        for tick in kline_ticks(kline, AGG_TRADE_SOURCE):
            oracle.observe(tick)
        count += 1
    return count


async def run_live_decisions(
    *,
    reader: BinancePriceReader,
    oracle: PriceOracle,
    offset_ns: int,
    on_decision: Callable[[dict[str, object]], None],
    last_decided_window_start_ns: int | None = None,
) -> None:
    """Record one Oracle decision per window, frozen at ``offset_ns`` before close."""

    decided_window_start_ns = last_decided_window_start_ns
    reader_task = asyncio.create_task(reader.run())
    try:
        while not reader.stopped:
            tick = await reader.queue.get()
            fair_value = oracle.observe(tick)
            if fair_value is None:
                continue
            if fair_value.remaining_ns > offset_ns:
                continue
            if fair_value.window_start_ns == decided_window_start_ns:
                continue
            decided_window_start_ns = fair_value.window_start_ns
            if not oracle.volatility_ready:
                LOGGER.warning(
                    "decision_before_volatility_warmup window=%s", decided_window_start_ns
                )
            on_decision(_decision_row(fair_value, offset_ns=offset_ns))
    finally:
        reader.stop()
        reader_task.cancel()
        await asyncio.gather(reader_task, return_exceptions=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record Oracle UP/DOWN decisions from live Binance data only "
            "(no Polymarket access); check outcomes by hand afterwards."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prediction-offset-s", type=float, default=60.0)
    parser.add_argument("--volatility-model", choices=VOLATILITY_MODELS, default=HORIZON_EWMA)
    parser.add_argument("--verbose", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> None:
    offset_ns = int(args.prediction_offset_s * 1_000_000_000)
    if offset_ns <= 0 or offset_ns >= WINDOW_NS:
        raise ValueError("prediction offset must be positive and shorter than five minutes")

    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    last_decided_window_start_ns = _last_window_start_ns(output_path)
    write_header = not output_path.exists() or output_path.stat().st_size == 0

    output_file = output_path.open("a", encoding="utf-8", newline="")
    try:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
            output_file.flush()

        def on_decision(row: dict[str, object]) -> None:
            writer.writerow(row)
            output_file.flush()
            LOGGER.info("live_decision %s", row)

        reader = BinancePriceReader()
        oracle = build_oracle(args.volatility_model)
        if args.volatility_model == HORIZON_EWMA:
            now_ms = int(time.time() * 1000)
            warmup_ms = int((DEFAULT_WARMUP_S + 60) * 1000)
            count = warm_up_oracle(oracle, fetch_klines_1s("BTCUSDT", now_ms - warmup_ms, now_ms))
            LOGGER.info("volatility_warmup klines=%d ready=%s", count, oracle.volatility_ready)
        await run_live_decisions(
            reader=reader,
            oracle=oracle,
            offset_ns=offset_ns,
            on_decision=on_decision,
            last_decided_window_start_ns=last_decided_window_start_ns,
        )
    finally:
        output_file.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        LOGGER.info("live_decisions_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
