"""Estimate a Polymarket token price at the T-60 backtest cutoff from trades."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_WINDOWS_SECONDS = (5, 15, 30, 60)
DEFAULT_PRIMARY_WINDOW_SECONDS = 15
EXPECTED_OFFSET_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class Trade:
    timestamp: float
    outcome: str
    side: str
    price: float
    size: float


def _parse_datetime(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a UTC offset: {value!r}")
    return parsed.astimezone(UTC)


def _format_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _finite_float(value: str, *, field: str, row_number: int) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"CSV row {row_number}: invalid {field} {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"CSV row {row_number}: non-finite {field}")
    return result


def _load_backtest_row(backtest_path: Path, market_id: str) -> dict[str, str]:
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
    with input_file:
        reader = csv.DictReader(input_file)
        fields = set(reader.fieldnames or ())
        missing = sorted(required - fields)
        if missing:
            raise ValueError("backtest CSV is missing columns: " + ", ".join(missing))
        matches = [row for row in reader if (row.get("market_id") or "").strip() == market_id]
    if len(matches) != 1:
        raise ValueError(f"expected one backtest row for market {market_id}, found {len(matches)}")

    row = matches[0]
    end = _parse_datetime(row["end_date_utc"], field="end_date_utc")
    cutoff = _parse_datetime(row["prediction_cutoff_utc"], field="prediction_cutoff_utc")
    offset = (end - cutoff).total_seconds()
    if not math.isclose(offset, EXPECTED_OFFSET_SECONDS, abs_tol=0.001):
        raise ValueError(
            f"market {market_id} cutoff is T-{offset:g}s, expected T-60s; "
            "generate a backtest with --prediction-offset-s 60"
        )
    return row


def _load_trades(trades_path: Path, expected_slug: str) -> tuple[list[Trade], dict[str, object]]:
    required = {"slug", "timestamp", "outcome", "side", "price", "size"}
    try:
        input_file = trades_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"cannot read Polymarket trades CSV: {exc}") from exc

    trades: list[Trade] = []
    rejected: Counter[str] = Counter()
    slugs: set[str] = set()
    row_count = 0
    with input_file:
        reader = csv.DictReader(input_file)
        fields = set(reader.fieldnames or ())
        missing = sorted(required - fields)
        if missing:
            raise ValueError("trades CSV is missing columns: " + ", ".join(missing))
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            slug = (row.get("slug") or "").strip()
            if slug:
                slugs.add(slug)
            if slug != expected_slug:
                rejected["slug_mismatch"] += 1
                continue
            outcome = (row.get("outcome") or "").strip().upper()
            if outcome not in {"UP", "DOWN"}:
                rejected["invalid_outcome"] += 1
                continue
            side = (row.get("side") or "").strip().upper()
            if side not in {"BUY", "SELL"}:
                rejected["invalid_side"] += 1
                continue
            try:
                timestamp = _finite_float(
                    row.get("timestamp") or "", field="timestamp", row_number=row_number
                )
                price = _finite_float(
                    row.get("price") or "", field="price", row_number=row_number
                )
                size = _finite_float(row.get("size") or "", field="size", row_number=row_number)
            except ValueError:
                rejected["invalid_numeric_value"] += 1
                continue
            if timestamp <= 0.0:
                rejected["invalid_timestamp"] += 1
                continue
            if not 0.0 <= price <= 1.0:
                rejected["invalid_price"] += 1
                continue
            if size <= 0.0:
                rejected["invalid_size"] += 1
                continue
            trades.append(Trade(timestamp, outcome, side, price, size))

    if slugs != {expected_slug}:
        raise ValueError(
            f"trades CSV slug does not match market {expected_slug!r}; "
            f"found {sorted(slugs)!r}"
        )
    return trades, {
        "rows_read": row_count,
        "valid_trades": len(trades),
        "rejected_rows_by_reason": dict(sorted(rejected.items())),
        "slugs": sorted(slugs),
    }


def _trade_summary(trades: list[Trade], outcome: str, cutoff: float) -> dict[str, object]:
    eligible = [trade for trade in trades if trade.outcome == outcome and trade.timestamp <= cutoff]
    latest = max(eligible, key=lambda trade: trade.timestamp) if eligible else None
    return {
        "trade_count_at_or_before_cutoff": len(eligible),
        "latest_trade": (
            {
                "timestamp_utc": _format_timestamp(latest.timestamp),
                "age_seconds_at_cutoff": cutoff - latest.timestamp,
                "side": latest.side,
                "price": latest.price,
                "size": latest.size,
            }
            if latest
            else None
        ),
    }


def _window_summary(
    trades: list[Trade], outcome: str, cutoff: float, window_seconds: int
) -> dict[str, object]:
    start = cutoff - window_seconds
    selected = [
        trade
        for trade in trades
        if trade.outcome == outcome and start < trade.timestamp <= cutoff
    ]
    if not selected:
        return {
            "window_seconds": window_seconds,
            "interval": "(cutoff - window, cutoff]",
            "trade_count": 0,
            "total_size": 0.0,
            "median_price": None,
            "size_weighted_vwap": None,
        }
    total_size = sum(trade.size for trade in selected)
    vwap = sum(trade.price * trade.size for trade in selected) / total_size
    return {
        "window_seconds": window_seconds,
        "interval": "(cutoff - window, cutoff]",
        "trade_count": len(selected),
        "total_size": total_size,
        "median_price": statistics.median(trade.price for trade in selected),
        "size_weighted_vwap": vwap,
    }


def analyze_market_price(
    market_id: str,
    trades_path: str | Path,
    backtest_path: str | Path,
    output_path: str | Path,
    *,
    windows_seconds: tuple[int, ...] = DEFAULT_WINDOWS_SECONDS,
    primary_window_seconds: int = DEFAULT_PRIMARY_WINDOW_SECONDS,
) -> dict[str, object]:
    """Estimate observed UP/DOWN prices at the T-60 cutoff from executions."""

    if not market_id.strip():
        raise ValueError("market_id cannot be empty")
    if not windows_seconds or any(window <= 0 for window in windows_seconds):
        raise ValueError("all price windows must be positive")
    if primary_window_seconds not in windows_seconds:
        raise ValueError("primary window must be included in windows_seconds")

    backtest_path = Path(backtest_path)
    trades_path = Path(trades_path)
    output_path = Path(output_path)
    backtest_row = _load_backtest_row(backtest_path, market_id)
    cutoff_dt = _parse_datetime(
        backtest_row["prediction_cutoff_utc"], field="prediction_cutoff_utc"
    )
    cutoff = cutoff_dt.timestamp()
    trades, trade_input = _load_trades(trades_path, backtest_row["slug"])

    before_cutoff = [trade for trade in trades if trade.timestamp <= cutoff]
    after_cutoff = [trade for trade in trades if trade.timestamp > cutoff]
    by_outcome = {
        outcome: {
            **_trade_summary(trades, outcome, cutoff),
            "trailing_windows": {
                str(window): _window_summary(trades, outcome, cutoff, window)
                for window in windows_seconds
            },
        }
        for outcome in ("UP", "DOWN")
    }

    primary_up = by_outcome["UP"]["trailing_windows"][str(primary_window_seconds)][
        "size_weighted_vwap"
    ]
    primary_down = by_outcome["DOWN"]["trailing_windows"][str(primary_window_seconds)][
        "size_weighted_vwap"
    ]
    implied_up_from_down = 1.0 - primary_down if primary_down is not None else None
    composite_up = (
        (primary_up + implied_up_from_down) / 2.0
        if primary_up is not None and implied_up_from_down is not None
        else None
    )
    try:
        oracle_up: float | None = float(backtest_row["prob_up"])
        oracle_down: float | None = float(backtest_row["prob_down"])
        if not math.isfinite(oracle_up) or not math.isfinite(oracle_down):
            raise ValueError
    except ValueError:
        oracle_up = None
        oracle_down = None

    report: dict[str, object] = {
        "market_id": market_id,
        "slug": backtest_row["slug"],
        "window_end_utc": backtest_row["end_date_utc"],
        "cutoff_utc": backtest_row["prediction_cutoff_utc"],
        "fair_value_timestamp_utc": backtest_row.get("fair_value_ts_utc") or None,
        "oracle_fair_value": {"prob_up": oracle_up, "prob_down": oracle_down},
        "trade_data": {
            **trade_input,
            "source_type": "executed trades tape",
            "executable_quote_available": False,
            "trades_at_or_before_cutoff": len(before_cutoff),
            "trades_after_cutoff_ignored": len(after_cutoff),
        },
        "prices_by_outcome": by_outcome,
        "primary_estimate": {
            "method": (
                "mean of UP size-weighted VWAP and 1 minus DOWN size-weighted VWAP "
                f"over the trailing {primary_window_seconds}s"
            ),
            "window_seconds": primary_window_seconds,
            "probability_up_from_up_vwap": primary_up,
            "probability_up_from_down_vwap": implied_up_from_down,
            "estimated_probability_up": composite_up,
            "oracle_probability_up": oracle_up,
            "difference_vs_oracle": (
                composite_up - oracle_up
                if composite_up is not None and oracle_up is not None
                else None
            ),
        },
        "interpretation": (
            "Trade-derived historical price proxy only. Trades are asynchronous executions, "
            "not order-book quotes; this report does not establish executable bid, ask, "
            "available depth, or fill probability. All trades after the cutoff are ignored."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate Polymarket UP/DOWN trade prices at a market's T-60 cutoff"
    )
    parser.add_argument("--market-id", required=True)
    parser.add_argument("--trades", type=Path)
    parser.add_argument(
        "--backtest", type=Path, default=Path("data/backtesting/backtest.csv")
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--primary-window-s", type=int, default=DEFAULT_PRIMARY_WINDOW_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    trades_path = args.trades or Path(f"data/backtesting/polymarket_trades_{args.market_id}.csv")
    output_path = args.output or Path(
        f"data/backtesting/polymarket_price_{args.market_id}_t60.json"
    )
    windows = tuple(sorted(set((*DEFAULT_WINDOWS_SECONDS, args.primary_window_s))))
    try:
        report = analyze_market_price(
            args.market_id,
            trades_path,
            args.backtest,
            output_path,
            windows_seconds=windows,
            primary_window_seconds=args.primary_window_s,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    primary = report["primary_estimate"]
    print(
        f"Market {args.market_id} at {report['cutoff_utc']}: "
        f"trade-derived UP={primary['estimated_probability_up']}; "
        f"Oracle UP={primary['oracle_probability_up']}; output={output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())