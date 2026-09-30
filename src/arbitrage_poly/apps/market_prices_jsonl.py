"""Calculate per-market UP/DOWN prices at a configured cutoff (T-120 by default)."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

EXPECTED_OFFSET_SECONDS = 120.0
DEFAULT_PRICE_WINDOW_SECONDS = 15


@dataclass(frozen=True, slots=True)
class MarketTarget:
    market_id: str
    slug: str
    cutoff_seconds: float
    cutoff_utc: str
    end_utc: str
    prob_up: float | None
    prob_down: float | None


@dataclass(slots=True)
class PriceAccumulator:
    count: int = 0
    total_size: float = 0.0
    price_size_sum: float = 0.0
    latest_timestamp: float | None = None
    latest_price: float | None = None
    latest_side: str | None = None
    latest_size: float | None = None

    def add(self, timestamp: float, price: float, size: float, side: str) -> None:
        self.count += 1
        self.total_size += size
        self.price_size_sum += price * size
        if self.latest_timestamp is None or timestamp >= self.latest_timestamp:
            self.latest_timestamp = timestamp
            self.latest_price = price
            self.latest_side = side
            self.latest_size = size

    @property
    def vwap(self) -> float | None:
        return self.price_size_sum / self.total_size if self.total_size else None


@dataclass(slots=True)
class MarketAccumulator:
    up: PriceAccumulator = field(default_factory=PriceAccumulator)
    down: PriceAccumulator = field(default_factory=PriceAccumulator)
    latest_up: PriceAccumulator = field(default_factory=PriceAccumulator)
    latest_down: PriceAccumulator = field(default_factory=PriceAccumulator)


def _parse_datetime(value: str, *, field_name: str, market_id: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"market {market_id}: invalid {field_name} {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"market {market_id}: {field_name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _optional_probability(value: str, *, field_name: str, market_id: str) -> float | None:
    if not value.strip():
        return None
    try:
        probability = float(value)
    except ValueError as exc:
        raise ValueError(f"market {market_id}: invalid {field_name} {value!r}") from exc
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError(f"market {market_id}: {field_name} must be between 0 and 1")
    return probability


def _load_market_targets(
    backtest_path: Path, prediction_offset_s: float
) -> tuple[dict[str, MarketTarget], dict[str, object]]:
    required = {
        "market_id",
        "slug",
        "end_date_utc",
        "prediction_cutoff_utc",
        "prob_up",
        "prob_down",
    }
    try:
        input_file = backtest_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"cannot read backtest CSV: {exc}") from exc

    targets: dict[str, MarketTarget] = {}
    by_slug: dict[str, MarketTarget] = {}
    total_rows = 0
    with input_file:
        reader = csv.DictReader(input_file)
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError("backtest CSV is missing columns: " + ", ".join(missing))
        for row_number, row in enumerate(reader, start=2):
            total_rows += 1
            market_id = (row.get("market_id") or "").strip()
            slug = (row.get("slug") or "").strip()
            if not market_id or not slug:
                raise ValueError(f"backtest CSV row {row_number}: market_id and slug are required")
            if market_id in targets:
                raise ValueError(f"duplicate market_id in backtest CSV: {market_id}")
            if slug in by_slug:
                raise ValueError(f"duplicate market slug in backtest CSV: {slug}")

            end = _parse_datetime(
                row["end_date_utc"], field_name="end_date_utc", market_id=market_id
            )
            cutoff = _parse_datetime(
                row["prediction_cutoff_utc"],
                field_name="prediction_cutoff_utc",
                market_id=market_id,
            )
            offset = (end - cutoff).total_seconds()
            if not math.isclose(offset, prediction_offset_s, abs_tol=0.001):
                raise ValueError(
                    f"market {market_id} cutoff is T-{offset:g}s, "
                    f"expected T-{prediction_offset_s:g}s"
                )
            target = MarketTarget(
                market_id=market_id,
                slug=slug,
                cutoff_seconds=cutoff.timestamp(),
                cutoff_utc=cutoff.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                end_utc=end.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                prob_up=_optional_probability(
                    row["prob_up"], field_name="prob_up", market_id=market_id
                ),
                prob_down=_optional_probability(
                    row["prob_down"], field_name="prob_down", market_id=market_id
                ),
            )
            targets[market_id] = target
            by_slug[slug] = target

    if not targets:
        raise ValueError("backtest CSV contains no markets")
    return by_slug, {
        "backtest_file": str(backtest_path),
        "markets_in_backtest": total_rows,
        "markets_selected": len(targets),
    }


def _parse_trade_identity(line: str, line_number: int) -> tuple[float, str, dict[str, object]]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSONL line {line_number}: invalid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError(f"JSONL line {line_number}: trade record must be a JSON object")

    try:
        timestamp = float(record["timestamp"])
        slug = str(record["slug"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"JSONL line {line_number}: missing or invalid trade identity") from exc
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError(f"JSONL line {line_number}: invalid timestamp")
    return timestamp, slug, record


def _parse_trade_values(
    record: dict[str, object], line_number: int
) -> tuple[str, str, float, float]:
    try:
        outcome = str(record["outcome"]).strip().upper()
        side = str(record["side"]).strip().upper()
        price = float(record["price"])
        size = float(record["size"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"JSONL line {line_number}: missing or invalid trade fields") from exc
    if not all(math.isfinite(value) for value in (price, size)):
        raise ValueError(f"JSONL line {line_number}: price and size must be finite")
    if not 0.0 <= price <= 1.0 or size <= 0:
        raise ValueError(f"JSONL line {line_number}: invalid price or size range")
    if outcome not in {"UP", "DOWN"}:
        raise ValueError(f"JSONL line {line_number}: invalid outcome {outcome!r}")
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"JSONL line {line_number}: invalid side {side!r}")
    return outcome, side, price, size


def _latest_trade(accumulator: PriceAccumulator, cutoff: float) -> dict[str, object] | None:
    if accumulator.latest_timestamp is None:
        return None
    return {
        "timestamp_utc": datetime.fromtimestamp(accumulator.latest_timestamp, UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "age_seconds_at_cutoff": cutoff - accumulator.latest_timestamp,
        "side": accumulator.latest_side,
        "price": accumulator.latest_price,
        "size": accumulator.latest_size,
    }


def _market_result(
    target: MarketTarget,
    accumulator: MarketAccumulator,
    window_seconds: int,
    prediction_offset_s: float,
) -> dict[str, object]:
    return {
        "market_id": target.market_id,
        "slug": target.slug,
        "cutoff_utc": target.cutoff_utc,
        "price_window_seconds": window_seconds,
        "price_method": (
            f"size-weighted VWAP of executions in "
            f"(T-{prediction_offset_s:g}s-{window_seconds}s, T-{prediction_offset_s:g}s]"
        ),
        "price_up": accumulator.up.vwap,
        "price_down": accumulator.down.vwap,
        "up_trade_count": accumulator.up.count,
        "down_trade_count": accumulator.down.count,
        "up_total_size": accumulator.up.total_size,
        "down_total_size": accumulator.down.total_size,
        "last_up_trade": _latest_trade(accumulator.latest_up, target.cutoff_seconds),
        "last_down_trade": _latest_trade(accumulator.latest_down, target.cutoff_seconds),
        "oracle_prob_up": target.prob_up,
        "oracle_prob_down": target.prob_down,
        "price_source": "executed trades; not an executable order-book quote",
    }


def analyze_jsonl_market_prices(
    trades_path: str | Path,
    backtest_path: str | Path,
    output_path: str | Path,
    *,
    window_seconds: int = DEFAULT_PRICE_WINDOW_SECONDS,
    prediction_offset_s: float = EXPECTED_OFFSET_SECONDS,
) -> dict[str, object]:
    """Stream sorted trades once and produce UP/DOWN price estimates per market."""

    if window_seconds <= 0:
        raise ValueError("price window must be positive")
    if not math.isfinite(prediction_offset_s) or not 0 < prediction_offset_s < 300:
        raise ValueError("prediction offset must be positive and shorter than five minutes")
    trades_path = Path(trades_path)
    backtest_path = Path(backtest_path)
    output_path = Path(output_path)
    targets_by_slug, backtest_summary = _load_market_targets(backtest_path, prediction_offset_s)
    targets = {target.market_id: target for target in targets_by_slug.values()}
    accumulators = {market_id: MarketAccumulator() for market_id in targets}

    try:
        trades_file: TextIO = trades_path.open("r", encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"cannot read trades JSONL: {exc}") from exc

    lines_scanned = 0
    priced_trades_validated = 0
    relevant_trades = 0
    ignored_after_cutoff = 0
    ignored_before_price_window = 0
    ignored_unknown_slug = 0
    completed_slugs: set[str] = set()
    last_timestamp_by_slug: dict[str, float] = {}
    with trades_file:
        for line_number, line in enumerate(trades_file, start=1):
            if not line.strip():
                continue
            lines_scanned += 1
            timestamp, slug, record = _parse_trade_identity(line, line_number)
            previous_timestamp = last_timestamp_by_slug.get(slug)
            if previous_timestamp is not None and timestamp > previous_timestamp:
                raise ValueError(
                    f"JSONL timestamps are not reverse-sorted within slug {slug!r} "
                    f"at line {line_number}"
                )
            last_timestamp_by_slug[slug] = timestamp

            target = targets_by_slug.get(slug)
            if target is None:
                ignored_unknown_slug += 1
                continue
            if slug in completed_slugs:
                continue
            if timestamp > target.cutoff_seconds:
                ignored_after_cutoff += 1
                continue
            if timestamp <= target.cutoff_seconds - window_seconds:
                ignored_before_price_window += 1
                completed_slugs.add(slug)
                continue

            outcome, side, price, size = _parse_trade_values(record, line_number)
            priced_trades_validated += 1
            accumulator = accumulators[target.market_id]
            price_accumulator = accumulator.up if outcome == "UP" else accumulator.down
            price_accumulator.add(timestamp, price, size, side)
            latest_accumulator = (
                accumulator.latest_up if outcome == "UP" else accumulator.latest_down
            )
            latest_accumulator.add(timestamp, price, size, side)
            relevant_trades += 1

    results = [
        _market_result(
            target, accumulators[target.market_id], window_seconds, prediction_offset_s
        )
        for target in sorted(targets.values(), key=lambda item: item.cutoff_seconds)
    ]
    report: dict[str, object] = {
        "price_window_seconds": window_seconds,
        "cutoff_offset_seconds": prediction_offset_s,
        "sort_order": (
            "descending Unix timestamps within each market; market groups may be unordered"
        ),
        "input": {
            "trades_file": str(trades_path),
            **backtest_summary,
            "lines_scanned": lines_scanned,
            "priced_trades_validated": priced_trades_validated,
            "relevant_window_trades": relevant_trades,
            "ignored_after_cutoff": ignored_after_cutoff,
            "ignored_before_price_window": ignored_before_price_window,
            "ignored_unknown_slug": ignored_unknown_slug,
            "markets_past_price_window_boundary": len(completed_slugs),
        },
        "interpretation": (
            "Prices are size-weighted VWAPs of executed trades in the configured window "
            f"ending at each market's T-{prediction_offset_s:g} cutoff. "
            "Null means no execution for that outcome "
            "in the window. Execution prices do not establish an available bid/ask or depth."
        ),
        "markets": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate UP/DOWN trade prices at the configured backtest cutoff"
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=Path("data/backtesting/polymarket_trades_window_10d.jsonl"),
    )
    parser.add_argument("--backtest", type=Path, default=Path("data/backtesting/backtest.csv"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--window-s", type=int, default=DEFAULT_PRICE_WINDOW_SECONDS)
    parser.add_argument(
        "--prediction-offset-s",
        type=float,
        default=EXPECTED_OFFSET_SECONDS,
        help="backtest cutoff offset in seconds (default: 120)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    output_path = args.output or Path(
        f"data/backtesting/market_prices_t{args.prediction_offset_s:g}.json"
    )
    try:
        report = analyze_jsonl_market_prices(
            args.trades,
            args.backtest,
            output_path,
            window_seconds=args.window_s,
            prediction_offset_s=args.prediction_offset_s,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    input_summary = report["input"]
    print(
        f"Wrote {len(report['markets'])} markets from {input_summary['lines_scanned']} "
        f"streamed trades to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
