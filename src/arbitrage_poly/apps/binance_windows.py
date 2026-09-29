"""Per five-minute window Oracle results from Binance REST 1s klines over the last N days.

Each window is scored against its Binance outcome (last 1s close vs first 1s open),
not the official Polymarket (Chainlink) resolution.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from tqdm import tqdm

from arbitrage_poly.apps.backtest import build_oracle
from arbitrage_poly.models import FairValue, Tick
from arbitrage_poly.oracle.reference import DEFAULT_WINDOW_NS
from arbitrage_poly.oracle.volatility import DEFAULT_WARMUP_S, HORIZON_EWMA, VOLATILITY_MODELS

KLINES_URL = "https://api.binance.com/api/v3/klines"
KLINES_LIMIT = 1000
KLINE_SOURCE = "binance.kline_1s"
KLINE_MODEL = "terminal_lognormal_ewma_kline_1s"
WINDOW_NS = DEFAULT_WINDOW_NS
WINDOW_MS = WINDOW_NS // 1_000_000
DAY_MS = 86_400_000

CSV_FIELDS = [
    "window_start_utc",
    "window_end_utc",
    "open_price",
    "close_price",
    "outcome_binance",
    "prediction_cutoff_utc",
    "fair_value_ts_utc",
    "reference_price",
    "current_price",
    "prob_up",
    "side",
    "volatility",
    "correct",
    "exclusion_reason",
]

Kline = tuple[int, float, float]  # (open_time_ms, open, close)


def _format_ms(ts_ms: int | None) -> str:
    if ts_ms is None:
        return ""
    return (
        datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def fetch_klines_1s(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    get_json: Callable[[str], list] | None = None,
) -> Iterator[Kline]:
    """Yield 1s klines with open time in [start_ms, end_ms), paging through the REST API."""

    def _default_get_json(url: str) -> list:
        with urlopen(url, timeout=30) as response:  # noqa: S310 - read-only public GET
            return json.loads(response.read())

    get_json = get_json or _default_get_json
    cursor = start_ms
    progress = tqdm(total=math.ceil((end_ms - start_ms) / 1000), unit="s", desc="Binance 1s")
    try:
        while cursor < end_ms:
            query = urlencode(
                {
                    "symbol": symbol,
                    "interval": "1s",
                    "startTime": cursor,
                    "endTime": end_ms - 1,
                    "limit": KLINES_LIMIT,
                }
            )
            rows = get_json(f"{KLINES_URL}?{query}")
            if not rows:
                break
            for row in rows:
                yield int(row[0]), float(row[1]), float(row[4])
            next_cursor = int(rows[-1][0]) + 1000
            progress.update((next_cursor - cursor) // 1000)
            cursor = next_cursor
    finally:
        progress.close()


def _outcome(open_price: float, close_price: float) -> str:
    if close_price > open_price:
        return "UP"
    if close_price < open_price:
        return "DOWN"
    return "TIE"


def _tick(ts_ms: int, price: float, source: str = KLINE_SOURCE) -> Tick:
    ts_ns = ts_ms * 1_000_000
    return Tick(ts_ns=ts_ns, price=price, qty=0.0, source=source, seq=None, exchange_ts_ns=ts_ns)


def kline_ticks(kline: Kline, source: str = KLINE_SOURCE) -> tuple[Tick, Tick]:
    """Open = first trade of the second; close stamped at its last ms (no look-ahead)."""

    open_ms, open_price, close_price = kline
    return _tick(open_ms, open_price, source), _tick(open_ms + 999, close_price, source)


def _window_row(
    window_start_ms: int,
    klines: list[Kline],
    cutoff_ms: int,
    fair_value: FairValue | None,
    volatility_ready: bool,
) -> dict[str, object]:
    window_end_ms = window_start_ms + WINDOW_MS
    row: dict[str, object] = {field: "" for field in CSV_FIELDS}
    row.update(
        window_start_utc=_format_ms(window_start_ms),
        window_end_utc=_format_ms(window_end_ms),
        prediction_cutoff_utc=_format_ms(cutoff_ms),
    )
    if not klines:
        row["exclusion_reason"] = "no_binance_klines"
        return row

    outcome = _outcome(klines[0][1], klines[-1][2])
    row.update(open_price=klines[0][1], close_price=klines[-1][2], outcome_binance=outcome)
    if fair_value is None:
        row["exclusion_reason"] = "no_prediction_before_cutoff"
        return row

    side = "UP" if fair_value.prob_up >= 0.5 else "DOWN"
    row.update(
        fair_value_ts_utc=_format_ms(fair_value.ts_ns // 1_000_000),
        reference_price=fair_value.reference_price,
        current_price=fair_value.current_price,
        prob_up=fair_value.prob_up,
        side=side,
        volatility=fair_value.volatility,
    )
    if not volatility_ready:
        row["exclusion_reason"] = "volatility_warmup"
    elif outcome == "TIE":
        row["exclusion_reason"] = "binance_tie"
    else:
        row["correct"] = side == outcome
    return row


def evaluate_windows(
    klines: Iterable[Kline],
    start_ms: int,
    end_ms: int,
    offset_ms: int,
    volatility_model: str = HORIZON_EWMA,
) -> Iterator[dict[str, object]]:
    """Score each five-minute window in [start_ms, end_ms) from chronological 1s klines.

    Klines before `start_ms` only warm up the continuous `horizon_ewma` volatility.
    """

    continuous = volatility_model == HORIZON_EWMA

    def new_oracle():
        return build_oracle(volatility_model, source=KLINE_SOURCE, model=KLINE_MODEL)

    oracle = new_oracle()
    iterator = iter(klines)
    pending = next(iterator, None)
    for window_start_ms in range(start_ms, end_ms, WINDOW_MS):
        while pending is not None and pending[0] < window_start_ms:
            if continuous:
                for tick in kline_ticks(pending):
                    oracle.observe(tick)
            pending = next(iterator, None)
        if not continuous:
            oracle = new_oracle()

        window_end_ms = window_start_ms + WINDOW_MS
        cutoff_ms = window_end_ms - offset_ms
        window_klines: list[Kline] = []
        fair_value: FairValue | None = None
        volatility_ready = False
        while pending is not None and pending[0] < window_end_ms:
            window_klines.append(pending)
            for tick in kline_ticks(pending):
                before_cutoff = tick.ts_ns < cutoff_ms * 1_000_000
                if continuous or before_cutoff:
                    observed = oracle.observe(tick)
                    if before_cutoff and observed is not None:
                        fair_value = observed
                        volatility_ready = oracle.volatility_ready
            pending = next(iterator, None)
        yield _window_row(window_start_ms, window_klines, cutoff_ms, fair_value, volatility_ready)


def _summary(rows: list[dict[str, object]]) -> list[tuple[str, int, float, float, int]]:
    """Return (day, scored, accuracy, brier, excluded) per UTC day plus a TOTAL line."""

    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[str(row["window_start_utc"])[:10]].append(row)
    groups_sorted = sorted(groups.items()) + [("TOTAL", rows)]
    lines = []
    for day, day_rows in groups_sorted:
        scored = [row for row in day_rows if row["correct"] != ""]
        n = len(scored)
        accuracy = sum(bool(row["correct"]) for row in scored) / n if n else math.nan
        brier = (
            sum((float(row["prob_up"]) - (row["outcome_binance"] == "UP")) ** 2 for row in scored)
            / n
            if n
            else math.nan
        )
        lines.append((day, n, accuracy, brier, len(day_rows) - n))
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, required=True)
    parser.add_argument("--prediction-offset-s", type=float, default=60.0)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--volatility-model", choices=VOLATILITY_MODELS, default=HORIZON_EWMA)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    offset_ms = int(args.prediction_offset_s * 1000)
    if args.days <= 0:
        parser.error("--days must be positive")
    if not 0 < offset_ms < WINDOW_MS:
        parser.error("prediction offset must be positive and shorter than five minutes")

    end_ms = int(time.time() * 1000) // WINDOW_MS * WINDOW_MS
    start_ms = end_ms - int(args.days * DAY_MS) // WINDOW_MS * WINDOW_MS
    warmup_ms = int(DEFAULT_WARMUP_S * 1000) if args.volatility_model == HORIZON_EWMA else 0
    klines = fetch_klines_1s(args.symbol, start_ms - warmup_ms, end_ms)
    rows = list(evaluate_windows(klines, start_ms, end_ms, offset_ms, args.volatility_model))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"{len(rows)} windows from {_format_ms(start_ms)} to {_format_ms(end_ms)}, "
        f"Oracle at T-{args.prediction_offset_s:g}s ({args.volatility_model}) vs Binance outcome"
    )
    print(f"{'utc_day':<10} {'scored':>6} {'acc':>7} {'brier':>7} {'excluded':>8}")
    for day, n, accuracy, brier, excluded in _summary(rows):
        print(f"{day:<10} {n:>6} {accuracy:>7.1%} {brier:>7.4f} {excluded:>8}")
    print(f"Per-window results: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
