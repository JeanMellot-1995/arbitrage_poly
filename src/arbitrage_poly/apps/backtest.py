"""Score historical Oracle predictions against resolved Polymarket markets."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

from tqdm import tqdm

from arbitrage_poly.models import FairValue, Tick
from arbitrage_poly.oracle.oracle import PriceOracle
from arbitrage_poly.oracle.reference import DEFAULT_WINDOW_NS
from arbitrage_poly.oracle.volatility import (
    HORIZON_EWMA,
    VOLATILITY_MODELS,
    HorizonEwmaVolatility,
)

WINDOW_NS = DEFAULT_WINDOW_NS
PREDICTION_OFFSET_NS = 120 * 1_000_000_000
VOLATILITY_SAMPLING_INTERVAL_NS = 1_000_000_000
AGG_TRADE_SOURCE = "binance.agg_trade"
AGG_TRADE_MODEL = "terminal_lognormal_ewma_agg_trade"
PROBABILITY_FLOOR = 0.001
PROBABILITY_CEILING = 0.999
UNCERTAIN_PRICE_BAND_LOW = 0.45
UNCERTAIN_PRICE_BAND_HIGH = 0.55
NANOSECONDS_PER_SECOND = 1_000_000_000
NANOSECONDS_PER_DAY = 86_400 * NANOSECONDS_PER_SECOND
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

CSV_FIELDS = [
    "market_id",
    "slug",
    "window_start_utc",
    "end_date_utc",
    "prediction_cutoff_utc",
    "fair_value_ts_utc",
    "reference_price",
    "current_price",
    "prob_up",
    "prob_down",
    "volatility",
    "outcome_polymarket",
    "outcome_binance",
    "scored",
    "exclusion_reason",
    "pnl_side",
    "pnl_exclusion_reason",
    "entry_price",
    "stake_usd",
    "shares",
    "payout_usd",
    "pnl_usd",
    "cumulative_pnl_usd",
]

STAKE_USD = 1.0


@dataclass(frozen=True, slots=True)
class MarketWindow:
    market_id: str
    slug: str
    end_ns: int
    outcome: str | None
    exclusion_reason: str | None

    @property
    def start_ns(self) -> int:
        return self.end_ns - WINDOW_NS


@dataclass(frozen=True, slots=True)
class AggTradeRow:
    row_number: int
    ts_ns: int
    price_text: str


@dataclass(slots=True)
class TradeStats:
    rows_scanned: int = 0
    rejected_rows: int = 0
    ticks_in_market_windows: int = 0
    first_ts_ns: int | None = None
    last_ts_ns: int | None = None


def _datetime_to_ns(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    delta = value.astimezone(UTC) - EPOCH
    return (
        (delta.days * 86_400 + delta.seconds) * NANOSECONDS_PER_SECOND
        + delta.microseconds * 1_000
    )


def _parse_datetime_ns(value: str) -> int:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid UTC timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a UTC offset: {value}")
    return _datetime_to_ns(parsed)


def _format_datetime_ns(value: int | None) -> str:
    if value is None:
        return ""
    dt = EPOCH + timedelta(microseconds=value // 1_000)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _binary_value(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip().lower()
    if text in {"up", "true"}:
        return 1
    if text in {"down", "false"}:
        return 0
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        return None
    if numeric == 1.0:
        return 1
    if numeric == 0.0:
        return 0
    return None


def _market_outcome(record: dict[str, object]) -> str | None:
    up = _binary_value(record.get("outcome_up"))
    down = _binary_value(record.get("outcome_down"))
    if up == 1 and down == 0:
        return "UP"
    if up == 0 and down == 1:
        return "DOWN"
    return None


def _load_markets(
    markets_path: Path,
    *,
    start_filter_ns: int | None,
    end_filter_ns: int | None,
) -> tuple[list[MarketWindow], int, int]:
    try:
        records = json.loads(markets_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read markets JSON: {exc}") from exc
    if not isinstance(records, list):
        raise ValueError("markets JSON must contain a top-level array")

    windows: list[MarketWindow] = []
    invalid_end_dates = 0
    date_filtered = 0
    for record in records:
        if not isinstance(record, dict):
            invalid_end_dates += 1
            continue
        try:
            end_ns = _parse_datetime_ns(str(record["endDate"]))
        except (KeyError, ValueError):
            invalid_end_dates += 1
            continue

        start_ns = end_ns - WINDOW_NS
        if start_filter_ns is not None and start_ns < start_filter_ns:
            date_filtered += 1
            continue
        if end_filter_ns is not None and start_ns >= end_filter_ns:
            date_filtered += 1
            continue

        resolved = str(record.get("resolution_status", "")).strip().lower() == "resolved"
        outcome = _market_outcome(record) if resolved else None
        reason = None if outcome else ("invalid_outcome" if resolved else "not_resolved")
        windows.append(
            MarketWindow(
                market_id=str(record.get("market_id", "")),
                slug=str(record.get("slug", "")),
                end_ns=end_ns,
                outcome=outcome,
                exclusion_reason=reason,
            )
        )

    windows.sort(key=lambda market: market.end_ns)
    previous_end_ns: int | None = None
    for market in windows:
        if previous_end_ns is not None and market.start_ns < previous_end_ns:
            raise ValueError("selected market windows overlap or have duplicate endDate values")
        if market.start_ns % WINDOW_NS != 0:
            raise ValueError("market windows must align to UTC five-minute boundaries")
        previous_end_ns = market.end_ns
    return windows, invalid_end_dates, date_filtered


def last_days_start_ns(markets_path: str | Path, days: float) -> int:
    """Return the window-start filter covering the last `days` before the latest market end."""

    if days <= 0:
        raise ValueError("last days must be positive")
    windows, _, _ = _load_markets(Path(markets_path), start_filter_ns=None, end_filter_ns=None)
    if not windows:
        raise ValueError("markets JSON contains no valid market windows")
    return windows[-1].end_ns - int(days * NANOSECONDS_PER_DAY)


def _iter_aggtrades(input_file: TextIO, stats: TradeStats):
    previous_ts_ns: int | None = None
    for row_number, row in enumerate(csv.reader(input_file), start=1):
        stats.rows_scanned += 1
        if len(row) != 8:
            stats.rejected_rows += 1
            continue
        try:
            ts_ns = int(row[5]) * 1_000
        except ValueError:
            stats.rejected_rows += 1
            continue
        if ts_ns <= 0:
            stats.rejected_rows += 1
            continue
        if previous_ts_ns is not None and ts_ns < previous_ts_ns:
            raise ValueError(f"aggTrades are not chronological at CSV row {row_number}")
        previous_ts_ns = ts_ns
        if stats.first_ts_ns is None:
            stats.first_ts_ns = ts_ns
        stats.last_ts_ns = ts_ns
        yield AggTradeRow(row_number=row_number, ts_ns=ts_ns, price_text=row[1])


def _valid_price(value: str) -> float | None:
    try:
        price = float(value)
    except ValueError:
        return None
    if not math.isfinite(price) or price <= 0.0:
        return None
    return price


def _agg_tick(ts_ns: int, price: float) -> Tick:
    return Tick(
        ts_ns=ts_ns,
        price=price,
        qty=0.0,
        source=AGG_TRADE_SOURCE,
        seq=None,
        exchange_ts_ns=ts_ns,
    )


def _binance_outcome(first_price: float | None, last_price: float | None) -> str | None:
    if first_price is None or last_price is None:
        return None
    if last_price > first_price:
        return "UP"
    if last_price < first_price:
        return "DOWN"
    return "TIE"


def _is_uncertain_price(price: float) -> bool:
    return UNCERTAIN_PRICE_BAND_LOW <= price <= UNCERTAIN_PRICE_BAND_HIGH


def build_oracle(
    volatility_model: str = HORIZON_EWMA,
    *,
    source: str = AGG_TRADE_SOURCE,
    model: str = AGG_TRADE_MODEL,
    probability_floor: float = PROBABILITY_FLOOR,
    probability_ceiling: float = PROBABILITY_CEILING,
) -> PriceOracle:
    """Build the Oracle shared by the backtest, Binance-window and live-decision apps."""

    if volatility_model not in VOLATILITY_MODELS:
        raise ValueError(f"volatility model must be one of {VOLATILITY_MODELS}")
    return PriceOracle(
        window_ns=WINDOW_NS,
        source=source,
        model=model,
        volatility_sampling_interval_ns=VOLATILITY_SAMPLING_INTERVAL_NS,
        probability_floor=probability_floor,
        probability_ceiling=probability_ceiling,
        volatility_estimator=(
            HorizonEwmaVolatility() if volatility_model == HORIZON_EWMA else None
        ),
    )


def _output_row(
    market: MarketWindow,
    *,
    prediction_cutoff_ns: int | None,
    fair_value: FairValue | None,
    outcome_binance: str | None,
    exclusion_reason: str | None,
    pnl_side: str | None,
    pnl_exclusion_reason: str | None,
    entry_price: float | None,
    shares: float | None,
    payout_usd: float | None,
    pnl_usd: float | None,
    cumulative_pnl_usd: float,
) -> dict[str, object]:
    return {
        "market_id": market.market_id,
        "slug": market.slug,
        "window_start_utc": _format_datetime_ns(market.start_ns),
        "end_date_utc": _format_datetime_ns(market.end_ns),
        "prediction_cutoff_utc": _format_datetime_ns(prediction_cutoff_ns),
        "fair_value_ts_utc": _format_datetime_ns(fair_value.ts_ns if fair_value else None),
        "reference_price": fair_value.reference_price if fair_value else "",
        "current_price": fair_value.current_price if fair_value else "",
        "prob_up": fair_value.prob_up if fair_value else "",
        "prob_down": fair_value.prob_down if fair_value else "",
        "volatility": fair_value.volatility if fair_value else "",
        "outcome_polymarket": market.outcome or "",
        "outcome_binance": outcome_binance or "",
        "scored": fair_value is not None and market.outcome is not None,
        "exclusion_reason": exclusion_reason or "",
        "pnl_side": pnl_side or "",
        "pnl_exclusion_reason": pnl_exclusion_reason or "",
        "entry_price": entry_price if entry_price is not None else "",
        "stake_usd": STAKE_USD if pnl_usd is not None else "",
        "shares": shares if shares is not None else "",
        "payout_usd": payout_usd if payout_usd is not None else "",
        "pnl_usd": pnl_usd if pnl_usd is not None else "",
        "cumulative_pnl_usd": cumulative_pnl_usd if pnl_usd is not None else "",
    }


def run_backtest(
    markets_path: str | Path,
    trades_path: str | Path,
    output_path: str | Path,
    report_path: str | Path,
    *,
    start_filter_ns: int | None = None,
    end_filter_ns: int | None = None,
    prediction_offset_ns: int = PREDICTION_OFFSET_NS,
    probability_floor: float = PROBABILITY_FLOOR,
    probability_ceiling: float = PROBABILITY_CEILING,
    volatility_model: str = HORIZON_EWMA,
) -> dict[str, object]:
    """Run a single-pass aggTrades backtest and write per-market and summary outputs.

    `horizon_ewma` keeps one Oracle fed with every tick (including before and between
    markets) so volatility carries across windows; `tick_ewma` resets it per market.
    """

    if prediction_offset_ns <= 0 or prediction_offset_ns >= WINDOW_NS:
        raise ValueError("prediction offset must be positive and shorter than five minutes")
    if not 0.0 <= probability_floor < probability_ceiling <= 1.0:
        raise ValueError("probability bounds must satisfy 0 <= floor < ceiling <= 1")
    continuous = volatility_model == HORIZON_EWMA
    if (
        start_filter_ns is not None
        and end_filter_ns is not None
        and start_filter_ns >= end_filter_ns
    ):
        raise ValueError("start date must be earlier than end date")

    markets_path = Path(markets_path)
    trades_path = Path(trades_path)
    output_path = Path(output_path)
    report_path = Path(report_path)
    markets, invalid_end_dates, date_filtered = _load_markets(
        markets_path,
        start_filter_ns=start_filter_ns,
        end_filter_ns=end_filter_ns,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    stats = TradeStats()
    exclusions: Counter[str] = Counter()
    brier_total = 0.0
    log_loss_total = 0.0
    correct_predictions = 0
    scored_windows = 0
    resolved_markets = 0
    valid_resolved_markets = 0
    outcome_mismatches = 0
    pnl_wins = 0
    pnl_losses = 0
    pnl_orders = 0
    pnl_exclusions: Counter[str] = Counter()
    total_payout_usd = 0.0
    total_pnl_usd = 0.0
    max_drawdown_usd = 0.0
    cumulative_pnl_usd = 0.0
    peak_pnl_usd = 0.0
    offset_ns = prediction_offset_ns

    try:
        trades_file = trades_path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise ValueError(f"cannot read Binance aggTrades CSV: {exc}") from exc

    with trades_file, output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        trade_rows = _iter_aggtrades(trades_file, stats)
        current_trade = next(trade_rows, None)

        def new_oracle() -> PriceOracle:
            return build_oracle(
                volatility_model,
                probability_floor=probability_floor,
                probability_ceiling=probability_ceiling,
            )

        oracle = new_oracle()
        for market in tqdm(markets, desc="Backtest Oracle", unit="market"):
            start_ns = market.start_ns
            end_ns = market.end_ns
            cutoff_ns = end_ns - offset_ns
            while current_trade is not None and current_trade.ts_ns < start_ns:
                if continuous:
                    price = _valid_price(current_trade.price_text)
                    if price is not None:
                        oracle.observe(_agg_tick(current_trade.ts_ns, price))
                current_trade = next(trade_rows, None)

            if not continuous:
                oracle = new_oracle()
            first_price: float | None = None
            last_price: float | None = None
            fair_value: FairValue | None = None
            volatility_ready = False
            while current_trade is not None and current_trade.ts_ns < end_ns:
                price = _valid_price(current_trade.price_text)
                if price is None:
                    stats.rejected_rows += 1
                else:
                    stats.ticks_in_market_windows += 1
                    if first_price is None:
                        first_price = price
                    last_price = price
                    before_cutoff = current_trade.ts_ns <= cutoff_ns
                    if continuous or before_cutoff:
                        observed = oracle.observe(_agg_tick(current_trade.ts_ns, price))
                        if before_cutoff and observed is not None:
                            fair_value = observed
                            volatility_ready = oracle.volatility_ready
                current_trade = next(trade_rows, None)

            binance_outcome = _binance_outcome(first_price, last_price)
            reason = market.exclusion_reason
            if reason is None and first_price is None:
                reason = "no_binance_ticks"
            if reason is None and fair_value is None:
                reason = "no_prediction_before_cutoff"
            if reason is None and not volatility_ready:
                reason = "volatility_warmup"

            if market.exclusion_reason is None:
                resolved_markets += 1
                if market.outcome is not None:
                    valid_resolved_markets += 1
            if reason is not None:
                exclusions[reason] += 1
            pnl_side: str | None = None
            entry_price: float | None = None
            shares: float | None = None
            payout_usd: float | None = None
            pnl_usd: float | None = None
            pnl_exclusion_reason: str | None = None
            if reason is None and fair_value is not None and market.outcome is not None:
                target = int(market.outcome == "UP")
                probability = fair_value.prob_up
                brier_total += (probability - target) ** 2
                clipped = min(1.0 - 1e-15, max(1e-15, probability))
                log_loss_total += -math.log(clipped if target else 1.0 - clipped)
                correct_predictions += int((probability >= 0.5) == bool(target))
                scored_windows += 1
                if binance_outcome in {"UP", "DOWN"} and binance_outcome != market.outcome:
                    outcome_mismatches += 1
                pnl_side = "UP" if probability >= 0.5 else "DOWN"
                entry_price = probability if pnl_side == "UP" else fair_value.prob_down
                if _is_uncertain_price(entry_price):
                    pnl_exclusion_reason = "uncertain_price_band"
                    pnl_exclusions[pnl_exclusion_reason] += 1
                else:
                    shares = STAKE_USD / entry_price
                    payout_usd = shares if market.outcome == pnl_side else 0.0
                    pnl_usd = payout_usd - STAKE_USD
                    cumulative_pnl_usd += pnl_usd
                    total_payout_usd += payout_usd
                    total_pnl_usd += pnl_usd
                    pnl_orders += 1
                    pnl_wins += int(pnl_usd > 0.0)
                    pnl_losses += int(pnl_usd < 0.0)
                    peak_pnl_usd = max(peak_pnl_usd, cumulative_pnl_usd)
                    max_drawdown_usd = min(
                        max_drawdown_usd, cumulative_pnl_usd - peak_pnl_usd
                    )

            writer.writerow(
                _output_row(
                    market,
                    prediction_cutoff_ns=cutoff_ns,
                    fair_value=fair_value,
                    outcome_binance=binance_outcome,
                    exclusion_reason=reason,
                    pnl_side=pnl_side,
                    pnl_exclusion_reason=pnl_exclusion_reason,
                    entry_price=entry_price,
                    shares=shares,
                    payout_usd=payout_usd,
                    pnl_usd=pnl_usd,
                    cumulative_pnl_usd=cumulative_pnl_usd,
                )
            )

    with output_path.open("a", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
        for _ in range(invalid_end_dates):
            exclusions["invalid_end_date"] += 1

    report: dict[str, object] = {
        "markets_file": str(markets_path),
        "trades_file": str(trades_path),
        "output_file": str(output_path),
        "prediction_offset_ns": offset_ns,
        "window_ns": WINDOW_NS,
        "model": AGG_TRADE_MODEL,
        "probability_floor": probability_floor,
        "probability_ceiling": probability_ceiling,
        "volatility_scope": (
            "continuous across markets; EWMA of squared 60s log returns, 15 min half-life, "
            "windows excluded until 1h of history"
            if continuous
            else "ticks inside each market window only; EWMA resets per market"
        ),
        "volatility_model": volatility_model,
        "volatility_sampling_interval_ns": VOLATILITY_SAMPLING_INTERVAL_NS,
        "start_filter_utc": _format_datetime_ns(start_filter_ns),
        "end_filter_utc_exclusive": _format_datetime_ns(end_filter_ns),
        "markets_selected": len(markets),
        "markets_date_filtered": date_filtered,
        "markets_invalid_end_date": invalid_end_dates,
        "resolved_markets": resolved_markets,
        "valid_resolved_markets": valid_resolved_markets,
        "scored_windows": scored_windows,
        "scoring_coverage": (
            scored_windows / valid_resolved_markets if valid_resolved_markets else None
        ),
        "accuracy": correct_predictions / scored_windows if scored_windows else None,
        "brier_score": brier_total / scored_windows if scored_windows else None,
        "log_loss": log_loss_total / scored_windows if scored_windows else None,
        "pnl_enabled": True,
        "pnl_stake_per_market_usd": STAKE_USD,
        "pnl_entry_price_source": "oracle fair value for the selected side",
        "pnl_uncertain_price_band_low": UNCERTAIN_PRICE_BAND_LOW,
        "pnl_uncertain_price_band_high": UNCERTAIN_PRICE_BAND_HIGH,
        "pnl_fee_rate": 0.0,
        "pnl_interpretation": (
            "theoretical only; entry price is the Oracle fair value, with no "
            "historical Polymarket execution price, fees, or slippage"
        ),
        "pnl_markets": pnl_orders,
        "pnl_excluded_by_reason": dict(sorted(pnl_exclusions.items())),
        "pnl_wins": pnl_wins,
        "pnl_losses": pnl_losses,
        "total_staked_usd": STAKE_USD * pnl_orders,
        "total_payout_usd": total_payout_usd,
        "total_pnl_usd": total_pnl_usd,
        "roi": total_pnl_usd / (STAKE_USD * pnl_orders) if pnl_orders else None,
        "max_drawdown_usd": max_drawdown_usd,
        "binance_polymarket_outcome_mismatches": outcome_mismatches,
        "excluded_windows_by_reason": dict(sorted(exclusions.items())),
        "aggtrade_rows_scanned": stats.rows_scanned,
        "aggtrade_rows_rejected": stats.rejected_rows,
        "ticks_in_market_windows": stats.ticks_in_market_windows,
        "first_scanned_tick_utc": _format_datetime_ns(stats.first_ts_ns),
        "last_scanned_tick_utc": _format_datetime_ns(stats.last_ts_ns),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest the BTC Oracle against resolved markets")
    parser.add_argument(
        "--markets",
        type=Path,
        default=Path("src/arbitrage_poly/data/polymarket-btc-5m-last-10d.json"),
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=Path("src/arbitrage_poly/data/BTCUSDT-aggTrades-concat.csv"),
    )
    parser.add_argument("--output", type=Path, default=Path("data/backtesting/backtest.csv"))
    parser.add_argument("--report", type=Path, default=Path("data/backtesting/backtest.json"))
    parser.add_argument("--start", help="Inclusive UTC market-window start (ISO 8601)")
    parser.add_argument("--end", help="Exclusive UTC market-window start (ISO 8601)")
    parser.add_argument(
        "--last-days",
        type=float,
        help="Only score markets from the last N days of the markets file (excludes --start)",
    )
    parser.add_argument("--prediction-offset-s", type=float, default=120.0)
    parser.add_argument("--probability-floor", type=float, default=PROBABILITY_FLOOR)
    parser.add_argument("--probability-ceiling", type=float, default=PROBABILITY_CEILING)
    parser.add_argument("--volatility-model", choices=VOLATILITY_MODELS, default=HORIZON_EWMA)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.last_days is not None and args.start:
        parser.error("--last-days and --start are mutually exclusive")
    try:
        start_ns = _parse_datetime_ns(args.start) if args.start else None
        if args.last_days is not None:
            start_ns = last_days_start_ns(args.markets, args.last_days)
        end_ns = _parse_datetime_ns(args.end) if args.end else None
        report = run_backtest(
            args.markets,
            args.trades,
            args.output,
            args.report,
            start_filter_ns=start_ns,
            end_filter_ns=end_ns,
            prediction_offset_ns=int(args.prediction_offset_s * NANOSECONDS_PER_SECOND),
            probability_floor=args.probability_floor,
            probability_ceiling=args.probability_ceiling,
            volatility_model=args.volatility_model,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Period from {report['start_filter_utc'] or 'file start'} "
        f"to {report['end_filter_utc_exclusive'] or 'file end'}; "
        f"offset T-{args.prediction_offset_s:g}s"
    )
    print(
        f"Scored {report['scored_windows']} of {report['valid_resolved_markets']} "
        f"resolved markets; accuracy={report['accuracy']}, "
        f"Brier={report['brier_score']}, log_loss={report['log_loss']}"
    )
    print(
        f"Theoretical PnL ($1/market at Oracle fair value): {report['total_pnl_usd']:+.2f} USD "
        f"over {report['pnl_markets']} bets, ROI={report['roi']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
