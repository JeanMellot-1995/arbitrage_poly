"""Replay archived Binance ticks through the pure price Oracle."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

from arbitrage_poly.models import FairValue, Tick
from arbitrage_poly.oracle.oracle import PriceOracle
from arbitrage_poly.oracle.reference import DEFAULT_WINDOW_NS, window_start_ns
from arbitrage_poly.oracle.volatility import SECONDS_PER_YEAR
from arbitrage_poly.polymarket import ClobHistoricalPriceClient, GammaMarketDiscovery
from arbitrage_poly.polymarket.cache import FileJsonCache
from arbitrage_poly.polymarket.economic_pricing import price_window_at_offset
from arbitrage_poly.polymarket.models import EconomicWindowPricing
from arbitrage_poly.polymarket.rest import RateLimitedRestClient
from arbitrage_poly.pricing import SizingConfig, compute_sizing

BOOK_TICKER_SOURCE = "binance.book_ticker"
AGG_TRADE_SOURCE = "binance.agg_trade"
AGG_TRADE_MODEL = "terminal_lognormal_ewma_agg_trade"
PREDICTION_OFFSET_NS = 60 * 1_000_000_000
DEFAULT_AGG_TRADE_SAMPLING_INTERVAL_NS = 1_000_000_000
DEFAULT_PROBABILITY_FLOOR = 0.05
DEFAULT_PROBABILITY_CEILING = 0.95
# Polymarket Crypto-category taker rate (see research.md, Décision 13). A
# historical replay has no order book, so every simulated fill is treated as
# a taker execution rather than guessing maker/taker from a single price.
DEFAULT_TAKER_FEE_RATE = 0.0007
# A token priced near a coin flip is treated as too uncertain to trade, even
# when it technically qualifies on edge alone (see research.md, Décision 14).
DEFAULT_UNCERTAIN_PRICE_BAND_LOW = 0.45
DEFAULT_UNCERTAIN_PRICE_BAND_HIGH = 0.55


@dataclass(frozen=True, slots=True)
class ReplayResult:
    fair_values: list[FairValue]
    statuses: dict[int, str]
    outcomes: dict[int, str | None]
    outcome_prices: dict[int, float | None]
    rejected_rows: list[int]
    ignored_sources: dict[str, int]
    partial_windows: list[int]
    complete_windows: list[int]


@dataclass(frozen=True, slots=True)
class ReplayReport:
    rows_read: int
    ticks_accepted: int
    rejected_rows: list[int]
    ignored_sources: dict[str, int]
    complete_windows: int
    partial_windows: int
    excluded_windows: int
    scored_windows: int
    prediction_offset_ns: int
    brier_score: float | None
    log_loss: float | None
    reliability: list[dict[str, float | int]]
    volatility_sampling_interval_ns: int | None
    probability_floor: float
    probability_ceiling: float
    min_edge: float = 0.0
    reversion_speed: float = 0.0
    uncertain_price_band_low: float = DEFAULT_UNCERTAIN_PRICE_BAND_LOW
    uncertain_price_band_high: float = DEFAULT_UNCERTAIN_PRICE_BAND_HIGH
    pnl_enabled: bool = False
    pnl_windows: int = 0
    pnl_wins: int = 0
    pnl_losses: int = 0
    pnl_ties: int = 0
    total_staked_usd: float = 0.0
    total_payout_usd: float = 0.0
    total_fees_usd: float = 0.0
    total_pnl_usd: float = 0.0
    max_drawdown_usd: float = 0.0
    oracle_scored_windows: int = 0
    economic_priced_windows: int = 0


def _optional_int(value: str, field_name: str) -> int | None:
    if value == "" or value.lower() in {"none", "null"}:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"invalid integer field: {field_name}") from exc


def _read_tick(row: dict[str, str | None]) -> Tick:
    source = row.get("source")
    if not source:
        raise ValueError("source is required")
    try:
        return Tick(
            ts_ns=int(row["ts_ns"] or ""),
            price=float(row["price"] or ""),
            qty=float(row["qty"] or ""),
            source=source,
            seq=_optional_int(row.get("seq") or "", "seq"),
            exchange_ts_ns=_optional_int(row.get("exchange_ts_ns") or "", "exchange_ts_ns"),
            received_ts_ns=_optional_int(row.get("received_ts_ns") or "", "received_ts_ns"),
        )
    except KeyError as exc:
        raise ValueError(f"missing column: {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def _load_normalized_ticks(
    input_file: TextIO,
    expected_source: str,
) -> tuple[list[Tick], int, list[int], dict[str, int]]:
    reader = csv.DictReader(input_file)
    required = {"ts_ns", "price", "qty", "source", "seq"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        missing = sorted(required - set(reader.fieldnames or []))
        raise ValueError(f"missing CSV columns: {', '.join(missing)}")

    ticks: list[Tick] = []
    rejected_rows: list[int] = []
    ignored_sources: dict[str, int] = defaultdict(int)
    previous_ts_ns: int | None = None
    rows_read = 0

    for row_number, row in enumerate(reader, start=2):
        rows_read += 1
        try:
            row_source = row.get("source") or ""
            if row_source != expected_source:
                ignored_sources[row_source] += 1
                continue
            tick = _read_tick(row)
            if previous_ts_ns is not None and tick.ts_ns < previous_ts_ns:
                raise ValueError("ts_ns is not monotone")
        except ValueError:
            rejected_rows.append(row_number)
            continue
        ticks.append(tick)
        previous_ts_ns = tick.ts_ns

    return ticks, rows_read, rejected_rows, dict(ignored_sources)


def _load_agg_trades(input_file: TextIO) -> tuple[list[Tick], int, list[int], dict[str, int]]:
    ticks: list[Tick] = []
    rejected_rows: list[int] = []
    previous_ts_ns: int | None = None
    rows_read = 0
    for row_number, row in enumerate(csv.reader(input_file), start=1):
        rows_read += 1
        try:
            if len(row) != 8:
                raise ValueError("expected 8 aggTrade columns")
            trade_id, price, quantity, _, _, timestamp_us, _, _ = row
            ts_ns = int(timestamp_us) * 1_000
            if previous_ts_ns is not None and ts_ns < previous_ts_ns:
                raise ValueError("timestamp is not monotone")
            tick = Tick(
                ts_ns=ts_ns,
                price=float(price),
                qty=float(quantity),
                source=AGG_TRADE_SOURCE,
                seq=int(trade_id),
                exchange_ts_ns=ts_ns,
            )
        except (TypeError, ValueError):
            rejected_rows.append(row_number)
            continue
        ticks.append(tick)
        previous_ts_ns = ts_ns
    return ticks, rows_read, rejected_rows, {}


def replay_ticks(
    ticks: list[Tick],
    *,
    window_ns: int = DEFAULT_WINDOW_NS,
    source: str = BOOK_TICKER_SOURCE,
    model: str | None = None,
    allow_terminal_window: bool = False,
    volatility_sampling_interval_ns: int | None = None,
    probability_floor: float = DEFAULT_PROBABILITY_FLOOR,
    probability_ceiling: float = DEFAULT_PROBABILITY_CEILING,
    reversion_speed: float = 0.0,
) -> ReplayResult:
    """Replay ordered ticks through the Oracle and classify their windows."""

    oracle = PriceOracle(
        window_ns=window_ns,
        source=source,
        model=model or "terminal_lognormal_ewma",
        volatility_sampling_interval_ns=volatility_sampling_interval_ns,
        probability_floor=probability_floor,
        probability_ceiling=probability_ceiling,
        reversion_speed=reversion_speed,
    )
    fair_values = [fair_value for tick in ticks if (fair_value := oracle.observe(tick)) is not None]
    grouped: dict[int, list[Tick]] = defaultdict(list)
    for tick in ticks:
        grouped[window_start_ns(tick.ts_ns, window_ns)].append(tick)

    timestamps = [tick.ts_ns for tick in ticks]
    statuses: dict[int, str] = {}
    outcomes: dict[int, str | None] = {}
    outcome_prices: dict[int, float | None] = {}
    complete_windows: list[int] = []
    partial_windows: list[int] = []

    for start_ns, window_ticks in grouped.items():
        end_ns = start_ns + window_ns
        has_end_observation = any(ts_ns >= end_ns for ts_ns in timestamps)
        is_terminal_window = start_ns == max(grouped)
        if not has_end_observation and not (allow_terminal_window and is_terminal_window):
            statuses[start_ns] = "partial"
            outcomes[start_ns] = None
            outcome_prices[start_ns] = None
            partial_windows.append(start_ns)
            continue

        outcome_candidates = [tick for tick in window_ticks if tick.ts_ns < end_ns]
        if not outcome_candidates:
            statuses[start_ns] = "excluded"
            outcomes[start_ns] = None
            outcome_prices[start_ns] = None
            continue
        reference_price = outcome_candidates[0].price
        outcome_price = outcome_candidates[-1].price
        if outcome_price > reference_price:
            outcome = "UP"
        elif outcome_price < reference_price:
            outcome = "DOWN"
        else:
            outcome = "TIE"
        statuses[start_ns] = "complete"
        outcomes[start_ns] = outcome
        outcome_prices[start_ns] = outcome_price
        complete_windows.append(start_ns)

    return ReplayResult(
        fair_values=fair_values,
        statuses=statuses,
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        rejected_rows=[],
        ignored_sources={},
        partial_windows=partial_windows,
        complete_windows=complete_windows,
    )


def _select_predictions(
    result: ReplayResult,
    *,
    window_ns: int,
    prediction_offset_ns: int,
) -> dict[int, FairValue]:
    """Select at most one FairValue per complete window, at or before the offset."""

    predictions_by_window: dict[int, FairValue] = {}
    for fair_value in result.fair_values:
        if result.statuses.get(fair_value.window_start_ns) != "complete":
            continue
        prediction_ts_ns = fair_value.window_start_ns + window_ns - prediction_offset_ns
        if fair_value.ts_ns <= prediction_ts_ns:
            previous = predictions_by_window.get(fair_value.window_start_ns)
            if previous is None or fair_value.ts_ns > previous.ts_ns:
                predictions_by_window[fair_value.window_start_ns] = fair_value
    return predictions_by_window


def _score(
    result: ReplayResult,
    *,
    window_ns: int = DEFAULT_WINDOW_NS,
    prediction_offset_ns: int = PREDICTION_OFFSET_NS,
) -> tuple[float | None, float | None, list[dict[str, float | int]], int]:
    predictions_by_window = _select_predictions(
        result, window_ns=window_ns, prediction_offset_ns=prediction_offset_ns
    )

    predictions: list[tuple[float, int]] = []
    for fair_value in predictions_by_window.values():
        outcome = result.outcomes[fair_value.window_start_ns]
        if outcome not in {"UP", "DOWN"}:
            continue
        predictions.append((fair_value.prob_up, int(outcome == "UP")))

    if not predictions:
        return None, None, [], 0
    brier = sum(
        (probability - outcome) ** 2 for probability, outcome in predictions
    ) / len(predictions)
    log_loss = sum(
        -math.log(max(1e-15, probability if outcome else 1.0 - probability))
        for probability, outcome in predictions
    ) / len(predictions)
    buckets: list[dict[str, float | int]] = []
    for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
        upper = lower + 0.2
        bucket = [
            (probability, outcome)
            for probability, outcome in predictions
            if lower <= probability < upper or (upper == 1.0 and probability == 1.0)
        ]
        if bucket:
            buckets.append(
                {
                    "lower": lower,
                    "upper": upper,
                    "count": len(bucket),
                    "predicted": sum(item[0] for item in bucket) / len(bucket),
                    "observed": sum(item[1] for item in bucket) / len(bucket),
                }
            )
    return brier, log_loss, buckets, len(predictions)


def _price_polymarket_windows(
    predictions_by_window: dict[int, FairValue],
    *,
    window_ns: int,
    prediction_offset_ns: int,
    symbol: str,
    discovery: GammaMarketDiscovery,
    price_client: ClobHistoricalPriceClient,
) -> dict[int, EconomicWindowPricing]:
    """Resolve Polymarket UP/DOWN prices at the offset for each predicted window."""

    return {
        window_start_ns: price_window_at_offset(
            window_start_ns=window_start_ns,
            window_end_ns=window_start_ns + window_ns,
            symbol=symbol,
            discovery=discovery,
            price_client=price_client,
            prediction_offset_ns=prediction_offset_ns,
        )
        for window_start_ns in predictions_by_window
    }


def _simulate_pnl(
    result: ReplayResult,
    *,
    window_ns: int,
    prediction_offset_ns: int,
    fee_rate: float,
    stake_usd: float | None = None,
    sizing_config: SizingConfig | None = None,
    entry_price: float | None = None,
    polymarket_pricing: dict[int, EconomicWindowPricing] | None = None,
    min_edge: float = 0.0,
    uncertain_price_band: tuple[float, float] = (
        DEFAULT_UNCERTAIN_PRICE_BAND_LOW,
        DEFAULT_UNCERTAIN_PRICE_BAND_HIGH,
    ),
) -> list[dict[str, float | int | str | None]]:
    """Simulate binary bets from the selected Oracle prediction.

    Without `polymarket_pricing`, `entry_price` is a synthetic constant used
    for sensitivity analysis, since Binance replay data has no historical
    Polymarket price. With `polymarket_pricing`, each window uses its own
    UP/DOWN price at `T-60s`; a window with a non-`priced` status is excluded
    from the bet (its Oracle score is unaffected) but still reported with its
    `economic_status`. A side is only bought when its own Oracle probability
    is strictly above 0.5, its executable entry price falls outside
    `uncertain_price_band` (a token priced near a coin flip is treated as too
    uncertain to trade regardless of edge), and its edge over the executable
    entry price is at least `min_edge` (`prob_up - up_price >= min_edge`,
    symmetrically for `DOWN`); no bet is placed otherwise. `fee_rate` is
    applied to the traded notional (the stake), matching the Polymarket taker
    rate; a historical replay has no order book, so every simulated fill is
    treated as a taker execution.

    Exactly one of `stake_usd` (fixed baseline) or `sizing_config` (dynamic
    Kelly-based sizing, see `pricing.sizing`) selects the stake per accepted
    window. Sizing never reopens the directional/edge decision already made
    above: it is only ever evaluated for a side that already qualifies.
    """
    predictions_by_window = _select_predictions(
        result, window_ns=window_ns, prediction_offset_ns=prediction_offset_ns
    )

    rows: list[dict[str, float | int | str | None]] = []
    cumulative_pnl = 0.0
    for prediction_window_start_ns in sorted(predictions_by_window):
        fair_value = predictions_by_window[prediction_window_start_ns]
        outcome = result.outcomes.get(prediction_window_start_ns)
        if outcome not in {"UP", "DOWN", "TIE"}:
            continue

        pricing = (
            polymarket_pricing.get(prediction_window_start_ns)
            if polymarket_pricing is not None
            else None
        )
        candidate_side = "UP" if fair_value.prob_up > 0.5 else "DOWN"
        if polymarket_pricing is not None and (pricing is None or not pricing.is_priced):
            rows.append(
                {
                    "window_start_ns": prediction_window_start_ns,
                    "prediction_ts_ns": fair_value.ts_ns,
                    "side": candidate_side,
                    "outcome": outcome,
                    "entry_price": None,
                    "stake_usd": None,
                    "shares": None,
                    "fees_usd": None,
                    "payout_usd": None,
                    "pnl_usd": None,
                    "cumulative_pnl_usd": cumulative_pnl,
                    "economic_status": pricing.status if pricing else "missing_market",
                    "market_id": pricing.market_id if pricing else None,
                }
            )
            continue

        up_price = pricing.up_price if pricing is not None else entry_price
        down_price = pricing.down_price if pricing is not None else entry_price
        band_low, band_high = uncertain_price_band
        up_in_band = band_low <= up_price <= band_high
        down_in_band = band_low <= down_price <= band_high
        up_qualifies = (
            fair_value.prob_up > 0.5
            and not up_in_band
            and (fair_value.prob_up - up_price) >= min_edge
        )
        down_qualifies = (
            fair_value.prob_down > 0.5
            and not down_in_band
            and (fair_value.prob_down - down_price) >= min_edge
        )

        if up_qualifies:
            side = "UP"
            window_entry_price = up_price
        elif down_qualifies:
            side = "DOWN"
            window_entry_price = down_price
        else:
            candidate_in_band = up_in_band if candidate_side == "UP" else down_in_band
            reason = "uncertain_price_band" if candidate_in_band else "edge_below_threshold"
            rows.append(
                {
                    "window_start_ns": prediction_window_start_ns,
                    "prediction_ts_ns": fair_value.ts_ns,
                    "side": candidate_side,
                    "outcome": outcome,
                    "entry_price": None,
                    "stake_usd": None,
                    "shares": None,
                    "fees_usd": None,
                    "payout_usd": None,
                    "pnl_usd": None,
                    "cumulative_pnl_usd": cumulative_pnl,
                    "economic_status": reason,
                    "market_id": pricing.market_id if pricing else None,
                }
            )
            continue

        fair_probability = fair_value.prob_up if side == "UP" else fair_value.prob_down
        if sizing_config is not None:
            sizing_decision = compute_sizing(
                accepted=True,
                config=sizing_config,
                probability=fair_probability,
                price=window_entry_price,
            )
            if sizing_decision.stake_usd <= 0.0:
                rows.append(
                    {
                        "window_start_ns": prediction_window_start_ns,
                        "prediction_ts_ns": fair_value.ts_ns,
                        "side": side,
                        "outcome": outcome,
                        "entry_price": window_entry_price,
                        "stake_usd": None,
                        "shares": None,
                        "fees_usd": None,
                        "payout_usd": None,
                        "pnl_usd": None,
                        "cumulative_pnl_usd": cumulative_pnl,
                        "kelly_fraction": sizing_decision.kelly_fraction,
                        "economic_status": sizing_decision.reason,
                        "market_id": pricing.market_id if pricing else None,
                    }
                )
                continue
            window_stake_usd = sizing_decision.stake_usd
            kelly_fraction = sizing_decision.kelly_fraction
        else:
            assert stake_usd is not None
            window_stake_usd = stake_usd
            kelly_fraction = None

        shares = window_stake_usd / window_entry_price
        fees = window_stake_usd * fee_rate
        payout = shares if outcome == side else 0.0
        pnl = payout - window_stake_usd - fees
        if outcome == "TIE":
            pnl = -fees
        cumulative_pnl += pnl
        row: dict[str, float | int | str | None] = {
            "window_start_ns": prediction_window_start_ns,
            "prediction_ts_ns": fair_value.ts_ns,
            "side": side,
            "outcome": outcome,
            "entry_price": window_entry_price,
            "stake_usd": window_stake_usd,
            "shares": shares,
            "fees_usd": fees,
            "payout_usd": payout,
            "pnl_usd": pnl,
            "cumulative_pnl_usd": cumulative_pnl,
            "kelly_fraction": kelly_fraction,
        }
        if pricing is not None:
            row.update(
                {
                    "economic_status": pricing.status,
                    "market_id": pricing.market_id,
                    "up_token_id": pricing.up_token_id,
                    "down_token_id": pricing.down_token_id,
                    "up_price": pricing.up_price,
                    "down_price": pricing.down_price,
                    "up_price_ts_ns": pricing.up_price_ts_ns,
                    "down_price_ts_ns": pricing.down_price_ts_ns,
                    "up_price_age_ns": pricing.up_price_age_ns,
                    "down_price_age_ns": pricing.down_price_age_ns,
                }
            )
        rows.append(row)
    return rows


def _max_drawdown(pnl_rows: list[dict[str, float | int | str | None]]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for row in pnl_rows:
        cumulative = float(row["cumulative_pnl_usd"])
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    return max_drawdown


def run_replay(
    input_path: Path,
    output_path: Path,
    report_path: Path | None = None,
    *,
    window_ns: int = DEFAULT_WINDOW_NS,
    input_format: str = "normalized",
    volatility_sampling_interval_ns: int | None = None,
    probability_floor: float = DEFAULT_PROBABILITY_FLOOR,
    probability_ceiling: float = DEFAULT_PROBABILITY_CEILING,
    entry_price: float | None = None,
    stake_usd: float | None = None,
    sizing_config: SizingConfig | None = None,
    fee_rate: float = 0.0,
    polymarket_symbol: str | None = None,
    polymarket_discovery: GammaMarketDiscovery | None = None,
    polymarket_price_client: ClobHistoricalPriceClient | None = None,
    min_edge: float = 0.0,
    reversion_speed: float = 0.0,
    uncertain_price_band_low: float = DEFAULT_UNCERTAIN_PRICE_BAND_LOW,
    uncertain_price_band_high: float = DEFAULT_UNCERTAIN_PRICE_BAND_HIGH,
    prediction_offset_ns: int = PREDICTION_OFFSET_NS,
) -> ReplayReport:
    with input_path.open(newline="", encoding="utf-8") as input_file:
        if input_format == "aggtrades":
            ticks, rows_read, rejected_rows, ignored_sources = _load_agg_trades(input_file)
            source = AGG_TRADE_SOURCE
            model = AGG_TRADE_MODEL
        else:
            ticks, rows_read, rejected_rows, ignored_sources = _load_normalized_ticks(
                input_file, BOOK_TICKER_SOURCE
            )
            source = BOOK_TICKER_SOURCE
            model = None
    sampling_interval_ns = (
        volatility_sampling_interval_ns
        if volatility_sampling_interval_ns is not None
        else (
            DEFAULT_AGG_TRADE_SAMPLING_INTERVAL_NS
            if input_format == "aggtrades"
            else None
        )
    )
    result = replay_ticks(
        ticks,
        window_ns=window_ns,
        source=source,
        model=model,
        allow_terminal_window=input_format == "aggtrades",
        volatility_sampling_interval_ns=sampling_interval_ns,
        probability_floor=probability_floor,
        probability_ceiling=probability_ceiling,
        reversion_speed=reversion_speed,
    )
    result = ReplayResult(
        fair_values=result.fair_values,
        statuses=result.statuses,
        outcomes=result.outcomes,
        outcome_prices=result.outcome_prices,
        rejected_rows=rejected_rows,
        ignored_sources=ignored_sources,
        partial_windows=result.partial_windows,
        complete_windows=result.complete_windows,
    )

    polymarket_enabled = bool(polymarket_discovery or polymarket_price_client or polymarket_symbol)
    if polymarket_enabled and not (
        polymarket_discovery and polymarket_price_client and polymarket_symbol
    ):
        raise ValueError(
            "polymarket_discovery, polymarket_price_client and polymarket_symbol must all be"
            " provided together"
        )
    if polymarket_enabled and entry_price is not None:
        raise ValueError("entry_price cannot be combined with live Polymarket pricing")
    if stake_usd is not None and sizing_config is not None:
        raise ValueError("stake_usd and sizing_config cannot both be provided")
    has_stake_source = stake_usd is not None or sizing_config is not None
    if polymarket_enabled and not has_stake_source:
        raise ValueError(
            "stake_usd or sizing_config is required when Polymarket pricing is enabled"
        )
    if not polymarket_enabled and (entry_price is None) != (not has_stake_source):
        raise ValueError(
            "entry_price and a stake source (stake_usd or sizing_config) must be provided together"
        )
    if entry_price is not None and (not 0.0 < entry_price <= 1.0):
        raise ValueError("entry_price must be in (0, 1]")
    if stake_usd is not None and stake_usd <= 0.0:
        raise ValueError("stake_usd must be positive")
    if fee_rate < 0.0:
        raise ValueError("fee_rate must be non-negative")
    if min_edge < 0.0:
        raise ValueError("min_edge must be non-negative")
    if not 0.0 <= uncertain_price_band_low <= uncertain_price_band_high <= 1.0:
        raise ValueError(
            "uncertain_price_band_low must be <= uncertain_price_band_high, both within [0, 1]"
        )
    if prediction_offset_ns <= 0:
        raise ValueError("prediction_offset_ns must be positive")
    if prediction_offset_ns >= window_ns:
        raise ValueError("prediction_offset_ns must be smaller than window_ns")

    pnl_rows: list[dict[str, float | int | str | None]] = []
    if has_stake_source and (entry_price is not None or polymarket_enabled):
        polymarket_pricing = None
        if polymarket_enabled:
            predictions_by_window = _select_predictions(
                result, window_ns=window_ns, prediction_offset_ns=prediction_offset_ns
            )
            polymarket_pricing = _price_polymarket_windows(
                predictions_by_window,
                window_ns=window_ns,
                prediction_offset_ns=prediction_offset_ns,
                symbol=polymarket_symbol,
                discovery=polymarket_discovery,
                price_client=polymarket_price_client,
            )
        pnl_rows = _simulate_pnl(
            result,
            window_ns=window_ns,
            prediction_offset_ns=prediction_offset_ns,
            entry_price=entry_price,
            stake_usd=stake_usd,
            sizing_config=sizing_config,
            fee_rate=fee_rate,
            polymarket_pricing=polymarket_pricing,
            min_edge=min_edge,
            uncertain_price_band=(uncertain_price_band_low, uncertain_price_band_high),
        )
    pnl_by_window = {row["window_start_ns"]: row for row in pnl_rows}
    priced_pnl_rows = [row for row in pnl_rows if row["pnl_usd"] is not None]
    predictions_by_window = _select_predictions(
        result, window_ns=window_ns, prediction_offset_ns=prediction_offset_ns
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        fieldnames = [
            "ts_ns", "prediction_ts_ns", "is_prediction", "window_start_ns", "reference_price",
            "current_price", "prob_up",
            "prob_up_raw",
            "prob_down", "model", "volatility", "remaining_ns", "window_status",
            "outcome", "outcome_price",
            "pnl_side", "entry_price", "stake_usd", "shares", "fees_usd",
            "payout_usd", "pnl_usd", "cumulative_pnl_usd", "kelly_fraction",
            "economic_status", "market_id", "up_token_id", "down_token_id",
            "up_price", "down_price", "up_price_ts_ns", "down_price_ts_ns",
            "up_price_age_ns", "down_price_age_ns",
        ]
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        emitted_pnl_windows: set[int] = set()
        for fair_value in result.fair_values:
            row = asdict(fair_value)
            selected_prediction = predictions_by_window.get(fair_value.window_start_ns)
            pnl_row = pnl_by_window.get(fair_value.window_start_ns)
            if (
                pnl_row is not None
                and (
                    pnl_row["prediction_ts_ns"] != fair_value.ts_ns
                    or fair_value.window_start_ns in emitted_pnl_windows
                )
            ):
                pnl_row = None
            elif pnl_row is not None:
                emitted_pnl_windows.add(fair_value.window_start_ns)
            row.update(
                {
                    "prediction_ts_ns": (
                        selected_prediction.ts_ns if selected_prediction is not None else None
                    ),
                    "is_prediction": selected_prediction is fair_value,
                    "window_status": result.statuses.get(fair_value.window_start_ns),
                    "outcome": result.outcomes.get(fair_value.window_start_ns),
                    "outcome_price": result.outcome_prices.get(fair_value.window_start_ns),
                    "pnl_side": pnl_row["side"] if pnl_row else None,
                    **(
                        {key: pnl_row[key] for key in fieldnames if key in pnl_row}
                        if pnl_row
                        else {}
                    ),
                }
            )
            writer.writerow(row)

    brier_score, log_loss, reliability, scored_windows = _score(
        result, window_ns=window_ns, prediction_offset_ns=prediction_offset_ns
    )
    report = ReplayReport(
        rows_read=rows_read,
        ticks_accepted=len(ticks),
        rejected_rows=rejected_rows,
        ignored_sources=ignored_sources,
        complete_windows=len(result.complete_windows),
        partial_windows=len(result.partial_windows),
        excluded_windows=sum(status == "excluded" for status in result.statuses.values()),
        scored_windows=scored_windows,
        prediction_offset_ns=prediction_offset_ns,
        brier_score=brier_score,
        log_loss=log_loss,
        reliability=reliability,
        volatility_sampling_interval_ns=sampling_interval_ns,
        probability_floor=probability_floor,
        probability_ceiling=probability_ceiling,
        min_edge=min_edge,
        reversion_speed=reversion_speed,
        uncertain_price_band_low=uncertain_price_band_low,
        uncertain_price_band_high=uncertain_price_band_high,
        pnl_enabled=entry_price is not None or polymarket_enabled,
        pnl_windows=len(priced_pnl_rows),
        pnl_wins=sum(row["pnl_usd"] > 0 for row in priced_pnl_rows),
        pnl_losses=sum(row["pnl_usd"] < 0 for row in priced_pnl_rows),
        pnl_ties=sum(row["outcome"] == "TIE" for row in priced_pnl_rows),
        total_staked_usd=sum(float(row["stake_usd"]) for row in priced_pnl_rows),
        total_payout_usd=sum(float(row["payout_usd"]) for row in priced_pnl_rows),
        total_fees_usd=sum(float(row["fees_usd"]) for row in priced_pnl_rows),
        total_pnl_usd=sum(float(row["pnl_usd"]) for row in priced_pnl_rows),
        max_drawdown_usd=_max_drawdown(priced_pnl_rows),
        oracle_scored_windows=scored_windows,
        economic_priced_windows=len(priced_pnl_rows) if polymarket_enabled else 0,
    )
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(asdict(report), indent=2, sort_keys=True), encoding="utf-8"
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--format",
        choices=("normalized", "aggtrades"),
        default="normalized",
        help="input format (default: normalized)",
    )
    parser.add_argument("--entry-price", type=float, help="synthetic Polymarket entry price")
    parser.add_argument(
        "--stake-usd",
        type=float,
        help="fixed stake per scored window (mutually exclusive with --bankroll-usd)",
    )
    parser.add_argument(
        "--bankroll-usd",
        type=float,
        help=(
            "enable dynamic Kelly-based sizing with this bankroll, instead of a fixed"
            " --stake-usd"
        ),
    )
    parser.add_argument(
        "--kelly-fraction-cap",
        type=float,
        default=0.5,
        help="fraction of the raw Kelly stake to risk in dynamic sizing (default: 0.5)",
    )
    parser.add_argument(
        "--max-stake-per-market",
        type=float,
        default=20.0,
        help="maximum stake per window in dynamic sizing (default: 20.0)",
    )
    parser.add_argument(
        "--min-stake-usd",
        type=float,
        default=1.0,
        help="minimum stake below which dynamic sizing returns Q=0 (default: 1.0)",
    )
    parser.add_argument(
        "--kill-switch-active",
        action="store_true",
        help="force dynamic sizing to return Q=0 for every window",
    )
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=DEFAULT_TAKER_FEE_RATE,
        help=(
            "taker fee rate applied to the traded notional (default: Polymarket"
            " Crypto-category taker rate, 0.0007); every simulated fill is treated"
            " as a taker execution since the replay has no order book"
        ),
    )
    parser.add_argument(
        "--polymarket-symbol",
        help="enable live Polymarket pricing at T-60s for this symbol (e.g. BTCUSDT)",
    )
    parser.add_argument(
        "--polymarket-cache",
        type=Path,
        help="JSON file of recorded Gamma/CLOB responses for deterministic offline replay",
    )
    parser.add_argument(
        "--polymarket-allow-network",
        action="store_true",
        help="allow the cache to fetch and record live responses on a miss",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=0.0,
        help=(
            "minimum (probability - price) edge required to place a bet"
            " (default: 0.0, i.e. any positive edge qualifies)"
        ),
    )
    parser.add_argument(
        "--mean-reversion-halflife-s",
        type=float,
        default=0.0,
        help=(
            "half-life in seconds for an Ornstein-Uhlenbeck-style mean-reversion"
            " adjustment to the Oracle's zero-drift probability model (default:"
            " 0.0, i.e. disabled, pure zero-drift random walk)"
        ),
    )
    parser.add_argument(
        "--uncertain-price-band-low",
        type=float,
        default=DEFAULT_UNCERTAIN_PRICE_BAND_LOW,
        help=(
            "lower bound of the entry-price band treated as too close to a coin"
            f" flip to trade, excluded regardless of edge (default:"
            f" {DEFAULT_UNCERTAIN_PRICE_BAND_LOW})"
        ),
    )
    parser.add_argument(
        "--uncertain-price-band-high",
        type=float,
        default=DEFAULT_UNCERTAIN_PRICE_BAND_HIGH,
        help=(
            "upper bound of the entry-price band treated as too close to a coin"
            f" flip to trade, excluded regardless of edge (default:"
            f" {DEFAULT_UNCERTAIN_PRICE_BAND_HIGH})"
        ),
    )
    parser.add_argument(
        "--prediction-offset-s",
        type=float,
        default=PREDICTION_OFFSET_NS / 1_000_000_000,
        help=(
            "how many seconds before window end the Oracle prediction and"
            " Polymarket price are taken (default: 60)"
        ),
    )
    args = parser.parse_args()

    polymarket_discovery = None
    polymarket_price_client = None
    if args.polymarket_symbol:
        rest_client_kwargs = {}
        if args.polymarket_cache:
            rest_client_kwargs["fetch_json"] = FileJsonCache(
                args.polymarket_cache, allow_network=args.polymarket_allow_network
            )
            if not args.polymarket_allow_network:
                # Strict offline replay only hits the local cache dict; the
                # inter-request throttle exists to protect the real API, and
                # a cache miss is a deterministic, non-retryable condition.
                rest_client_kwargs["min_request_interval_s"] = 0.0
                rest_client_kwargs["max_retries"] = 0
        rest_client = RateLimitedRestClient(**rest_client_kwargs)
        polymarket_discovery = GammaMarketDiscovery(client=rest_client)
        polymarket_price_client = ClobHistoricalPriceClient(client=rest_client)

    sizing_config = None
    if args.bankroll_usd is not None:
        sizing_config = SizingConfig(
            mode="dynamic",
            bankroll=args.bankroll_usd,
            kelly_fraction_cap=args.kelly_fraction_cap,
            max_stake_per_market=args.max_stake_per_market,
            min_stake_usd=args.min_stake_usd,
            kill_switch_active=args.kill_switch_active,
        )

    if args.mean_reversion_halflife_s < 0.0:
        parser.error("--mean-reversion-halflife-s must be non-negative")
    reversion_speed = 0.0
    if args.mean_reversion_halflife_s > 0.0:
        halflife_years = args.mean_reversion_halflife_s / SECONDS_PER_YEAR
        reversion_speed = math.log(2.0) / halflife_years

    if args.prediction_offset_s <= 0.0:
        parser.error("--prediction-offset-s must be positive")

    report = run_replay(
        args.input,
        args.output,
        args.report,
        input_format=args.format,
        entry_price=args.entry_price,
        stake_usd=args.stake_usd,
        sizing_config=sizing_config,
        fee_rate=args.fee_rate,
        polymarket_symbol=args.polymarket_symbol,
        polymarket_discovery=polymarket_discovery,
        polymarket_price_client=polymarket_price_client,
        min_edge=args.min_edge,
        reversion_speed=reversion_speed,
        uncertain_price_band_low=args.uncertain_price_band_low,
        uncertain_price_band_high=args.uncertain_price_band_high,
        prediction_offset_ns=int(args.prediction_offset_s * 1_000_000_000),
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
