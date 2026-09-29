"""Collect a short live Binance BTCUSDT sample into CSV."""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from arbitrage_poly.models import Tick
from arbitrage_poly.price_collection.binance_ws import BinancePriceReader

CSV_FIELDS = [
    "ts_ns",
    "exchange_ts_ns",
    "received_ts_ns",
    "price",
    "qty",
    "source",
    "seq",
]


def write_tick(writer: csv.DictWriter, tick: Tick) -> None:
    writer.writerow(
        {
            "ts_ns": tick.ts_ns,
            "exchange_ts_ns": tick.exchange_ts_ns,
            "received_ts_ns": tick.received_ts_ns,
            "price": tick.price,
            "qty": tick.qty,
            "source": tick.source,
            "seq": tick.seq,
        }
    )


async def collect(output: Path, duration: float) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    queue = asyncio.Queue(maxsize=10_000)
    reader = BinancePriceReader(queue=queue)
    reader_task = asyncio.create_task(reader.run())
    row_count = 0

    try:
        with output.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            deadline = asyncio.get_running_loop().time() + duration
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    tick = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    break
                write_tick(writer, tick)
                row_count += 1
    finally:
        reader.stop()
        reader_task.cancel()
        await asyncio.gather(reader_task, return_exceptions=True)

    print(f"Wrote {row_count} ticks to {output}")
    print(
        "Metrics: "
        f"messages={reader.metrics.message_count} "
        f"rejected={reader.metrics.rejected_count} "
        f"sequence_anomalies={reader.metrics.sequence_gap_count} "
        f"reconnects={reader.metrics.reconnect_count} "
        f"last_error={reader.state.last_error!r}"
    )
    return row_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="collection duration in seconds (default: 10)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/binance_sample.csv"),
        help="CSV output path (default: data/binance_sample.csv)",
    )
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be positive")
    return args


def main() -> None:
    args = parse_args()
    asyncio.run(collect(args.output, args.duration))


if __name__ == "__main__":
    main()
