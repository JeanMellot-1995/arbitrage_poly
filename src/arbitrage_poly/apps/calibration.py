"""Analyze calibration of per-market Oracle backtest predictions."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LOG_EPSILON = 1e-15
UNCERTAIN_BAND_LOW = 0.45
UNCERTAIN_BAND_HIGH = 0.55
DEFAULT_BOOTSTRAP_REPLICATES = 1_000
DEFAULT_BOOTSTRAP_SEED = 42


@dataclass(frozen=True, slots=True)
class Prediction:
    market_id: str
    day: str
    probability_up: float
    outcome_up: int
    pnl: float | None
    pnl_exclusion_reason: str
    stake: float | None


@dataclass(frozen=True, slots=True)
class ProbabilityBucket:
    label: str
    lower: float
    upper: float
    include_lower: bool
    include_upper: bool

    def contains(self, value: float) -> bool:
        above_lower = value >= self.lower if self.include_lower else value > self.lower
        below_upper = value <= self.upper if self.include_upper else value < self.upper
        return above_lower and below_upper


PROBABILITY_BUCKETS = (
    ProbabilityBucket("[0.00,0.05)", 0.00, 0.05, True, False),
    ProbabilityBucket("[0.05,0.10)", 0.05, 0.10, True, False),
    ProbabilityBucket("[0.10,0.20)", 0.10, 0.20, True, False),
    ProbabilityBucket("[0.20,0.30)", 0.20, 0.30, True, False),
    ProbabilityBucket("[0.30,0.40)", 0.30, 0.40, True, False),
    ProbabilityBucket("[0.40,0.45)", 0.40, 0.45, True, False),
    ProbabilityBucket("[0.45,0.50)", 0.45, 0.50, True, False),
    ProbabilityBucket("[0.50,0.55]", 0.50, 0.55, True, True),
    ProbabilityBucket("(0.55,0.60)", 0.55, 0.60, False, False),
    ProbabilityBucket("[0.60,0.70)", 0.60, 0.70, True, False),
    ProbabilityBucket("[0.70,0.80)", 0.70, 0.80, True, False),
    ProbabilityBucket("[0.80,0.90)", 0.80, 0.90, True, False),
    ProbabilityBucket("[0.90,0.95]", 0.90, 0.95, True, True),
    ProbabilityBucket("(0.95,1.00]", 0.95, 1.00, False, True),
)

CONFIDENCE_BUCKETS = (
    ProbabilityBucket("[0.50,0.55]", 0.50, 0.55, True, True),
    ProbabilityBucket("(0.55,0.65]", 0.55, 0.65, False, True),
    ProbabilityBucket("(0.65,0.75]", 0.65, 0.75, False, True),
    ProbabilityBucket("(0.75,0.85]", 0.75, 0.85, False, True),
    ProbabilityBucket("(0.85,0.95]", 0.85, 0.95, False, True),
    ProbabilityBucket("(0.95,1.00]", 0.95, 1.00, False, True),
)

TABLE_FIELDS = (
    "group_type",
    "bucket",
    "n",
    "predicted_mean",
    "observed_rate",
    "calibration_gap",
    "accuracy",
    "brier_score",
    "log_loss",
    "ece_contribution",
    "pnl_bets",
    "pnl_excluded_uncertain",
    "pnl_missing",
    "total_staked_usd",
    "total_pnl_usd",
    "roi",
)
DAILY_FIELDS = (
    "utc_day",
    "n",
    "up_count",
    "prevalence_up",
    "accuracy",
    "brier_score",
    "log_loss",
    "walk_forward_train_n",
    "walk_forward_prevalence_up",
    "walk_forward_baseline_brier",
    "oracle_brier_skill_score",
    "pnl_bets",
    "pnl_excluded_uncertain",
    "pnl_missing",
    "total_staked_usd",
    "total_pnl_usd",
    "roi",
)


def _parse_binary_outcome(value: str) -> int | None:
    normalized = value.strip().lower()
    if normalized in {"up", "true", "1"}:
        return 1
    if normalized in {"down", "false", "0"}:
        return 0
    return None


def _parse_utc_day(value: str, *, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a UTC offset: {value!r}")
    return parsed.astimezone(UTC).date().isoformat()


def _finite_number(value: str, *, field: str, market_id: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"market {market_id!r} has invalid {field}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"market {market_id!r} has non-finite {field}")
    return parsed


def _load_predictions(input_path: Path) -> tuple[list[Prediction], dict[str, object]]:
    required = {
        "market_id",
        "window_start_utc",
        "prob_up",
        "outcome_polymarket",
        "scored",
        "exclusion_reason",
    }
    economic_columns = {"pnl_exclusion_reason", "pnl_usd", "stake_usd"}
    predictions: list[Prediction] = []
    exclusions: Counter[str] = Counter()
    market_ids: set[str] = set()
    total_rows = 0
    try:
        input_file = input_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"cannot read backtest CSV: {exc}") from exc

    with input_file:
        reader = csv.DictReader(input_file)
        fields = set(reader.fieldnames or ())
        missing = sorted(required - fields)
        if missing:
            raise ValueError(f"backtest CSV is missing required columns: {', '.join(missing)}")
        present_economic_columns = economic_columns & fields
        if present_economic_columns and present_economic_columns != economic_columns:
            missing_economic = sorted(economic_columns - present_economic_columns)
            raise ValueError(
                "backtest CSV has an incomplete P&L schema; missing columns: "
                + ", ".join(missing_economic)
            )
        pnl_data_available = present_economic_columns == economic_columns

        for row_number, row in enumerate(reader, start=2):
            total_rows += 1
            market_id = (row.get("market_id") or "").strip()
            if not market_id:
                raise ValueError(f"CSV row {row_number} has an empty market_id")
            if market_id in market_ids:
                raise ValueError(f"duplicate market_id in backtest CSV: {market_id!r}")
            market_ids.add(market_id)

            scored_text = (row.get("scored") or "").strip().lower()
            if scored_text not in {"true", "false"}:
                raise ValueError(f"market {market_id!r} has invalid scored value {scored_text!r}")
            if scored_text == "false":
                reason = (row.get("exclusion_reason") or "unspecified").strip() or "unspecified"
                exclusions[reason] += 1
                continue

            day = _parse_utc_day(row.get("window_start_utc") or "", field="window_start_utc")
            probability = _finite_number(
                row.get("prob_up") or "", field="prob_up", market_id=market_id
            )
            if not 0.0 <= probability <= 1.0:
                raise ValueError(f"market {market_id!r} has prob_up outside [0, 1]")
            outcome = _parse_binary_outcome(row.get("outcome_polymarket") or "")
            if outcome is None:
                raise ValueError(f"market {market_id!r} has invalid outcome_polymarket")

            pnl_reason = (row.get("pnl_exclusion_reason") or "").strip()
            pnl_text = (row.get("pnl_usd") or "").strip()
            stake_text = (row.get("stake_usd") or "").strip()
            pnl = (
                _finite_number(pnl_text, field="pnl_usd", market_id=market_id) if pnl_text else None
            )
            stake = (
                _finite_number(stake_text, field="stake_usd", market_id=market_id)
                if stake_text
                else None
            )
            predictions.append(
                Prediction(market_id, day, probability, outcome, pnl, pnl_reason, stake)
            )

    return predictions, {
        "input_rows": total_rows,
        "scored_rows": len(predictions),
        "excluded_rows": total_rows - len(predictions),
        "excluded_rows_by_reason": dict(sorted(exclusions.items())),
        "pnl_data_available": pnl_data_available,
    }


def _metrics(pairs: Iterable[tuple[float, int]]) -> dict[str, float | int | None]:
    values = list(pairs)
    count = len(values)
    if not count:
        return {
            "n": 0,
            "predicted_mean": None,
            "observed_rate": None,
            "calibration_gap": None,
            "accuracy": None,
            "brier_score": None,
            "log_loss": None,
        }
    predicted_mean = sum(probability for probability, _ in values) / count
    observed_rate = sum(outcome for _, outcome in values) / count
    brier = sum((probability - outcome) ** 2 for probability, outcome in values) / count
    log_loss = 0.0
    correct = 0
    for probability, outcome in values:
        clipped = min(1.0 - LOG_EPSILON, max(LOG_EPSILON, probability))
        log_loss -= outcome * math.log(clipped) + (1 - outcome) * math.log(1.0 - clipped)
        correct += int((probability >= 0.5) == bool(outcome))
    return {
        "n": count,
        "predicted_mean": predicted_mean,
        "observed_rate": observed_rate,
        "calibration_gap": predicted_mean - observed_rate,
        "accuracy": correct / count,
        "brier_score": brier,
        "log_loss": log_loss / count,
    }


def _pnl_summary(
    predictions: Iterable[Prediction], *, available: bool = True
) -> dict[str, float | int | None]:
    values = list(predictions)
    if not available:
        return {
            "pnl_bets": None,
            "pnl_excluded_uncertain": None,
            "pnl_missing": None,
            "total_staked_usd": None,
            "total_pnl_usd": None,
            "roi": None,
        }
    bets = [prediction for prediction in values if prediction.pnl is not None]
    excluded = sum(
        prediction.pnl_exclusion_reason == "uncertain_price_band" for prediction in values
    )
    staked = sum(prediction.stake or 0.0 for prediction in bets)
    pnl = sum(prediction.pnl or 0.0 for prediction in bets)
    return {
        "pnl_bets": len(bets),
        "pnl_excluded_uncertain": excluded,
        "pnl_missing": len(values) - len(bets) - excluded,
        "total_staked_usd": staked,
        "total_pnl_usd": pnl,
        "roi": pnl / staked if staked else None,
    }


def _group_row(
    group_type: str,
    bucket: str,
    predictions: list[Prediction],
    pairs: list[tuple[float, int]],
    *,
    pnl_available: bool,
) -> dict[str, object]:
    metrics = _metrics(pairs)
    if group_type == "confidence":
        metrics["accuracy"] = None
    count = metrics["n"]
    gap = metrics["calibration_gap"]
    return {
        "group_type": group_type,
        "bucket": bucket,
        **metrics,
        "ece_contribution": abs(gap) * count if gap is not None else None,
        **_pnl_summary(predictions, available=pnl_available),
    }


def _bucket_rows(
    predictions: list[Prediction],
    buckets: tuple[ProbabilityBucket, ...],
    *,
    confidence: bool,
    pnl_available: bool,
) -> list[dict[str, object]]:
    rows = []
    for bucket in buckets:
        selected_predictions: list[Prediction] = []
        metric_pairs: list[tuple[float, int]] = []
        for prediction in predictions:
            if confidence:
                forecast_side = int(prediction.probability_up >= 0.5)
                forecast = max(prediction.probability_up, 1.0 - prediction.probability_up)
                outcome = int(forecast_side == prediction.outcome_up)
            else:
                forecast = prediction.probability_up
                outcome = prediction.outcome_up
            if bucket.contains(forecast):
                selected_predictions.append(prediction)
                metric_pairs.append((forecast, outcome))
        rows.append(
            _group_row(
                "confidence" if confidence else "probability_up",
                bucket.label,
                selected_predictions,
                metric_pairs,
                pnl_available=pnl_available,
            )
        )
    return rows


def _daily_rows(
    predictions: list[Prediction], *, pnl_available: bool
) -> tuple[list[dict[str, object]], list[Prediction]]:
    grouped: dict[str, list[Prediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[prediction.day].append(prediction)

    rows: list[dict[str, object]] = []
    prior_outcomes: list[int] = []
    walk_forward_predictions: list[Prediction] = []
    for day in sorted(grouped):
        values = grouped[day]
        metrics = _metrics(
            (prediction.probability_up, prediction.outcome_up) for prediction in values
        )
        prevalence = sum(prior_outcomes) / len(prior_outcomes) if prior_outcomes else None
        baseline_pairs = [(prevalence, prediction.outcome_up) for prediction in values]
        baseline_brier = (
            sum((forecast - outcome) ** 2 for forecast, outcome in baseline_pairs) / len(values)
            if prevalence is not None
            else None
        )
        skill = (
            1.0 - float(metrics["brier_score"]) / baseline_brier
            if baseline_brier is not None and baseline_brier > 0.0
            else None
        )
        pnl = _pnl_summary(values, available=pnl_available)
        rows.append(
            {
                "utc_day": day,
                "n": len(values),
                "up_count": sum(prediction.outcome_up for prediction in values),
                "prevalence_up": metrics["observed_rate"],
                "accuracy": metrics["accuracy"],
                "brier_score": metrics["brier_score"],
                "log_loss": metrics["log_loss"],
                "walk_forward_train_n": len(prior_outcomes),
                "walk_forward_prevalence_up": prevalence,
                "walk_forward_baseline_brier": baseline_brier,
                "oracle_brier_skill_score": skill,
                **pnl,
            }
        )
        if prevalence is not None:
            walk_forward_predictions.extend(values)
        prior_outcomes.extend(prediction.outcome_up for prediction in values)
    return rows, walk_forward_predictions


def _baseline_metrics(
    predictions: list[Prediction], probability: float
) -> dict[str, float | int | None]:
    pairs = [(probability, prediction.outcome_up) for prediction in predictions]
    return _metrics(pairs)


def _percentile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def _bootstrap_intervals(
    predictions: list[Prediction], *, replicates: int, seed: int
) -> dict[str, object]:
    grouped: dict[str, list[Prediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[prediction.day].append(prediction)
    days = sorted(grouped)
    result: dict[str, object] = {
        "method": "UTC-day cluster bootstrap with replacement",
        "confidence_level": 0.95,
        "block_unit": "UTC day",
        "available_blocks": len(days),
        "replicates": replicates,
        "seed": seed,
        "exploratory": len(days) < 2,
        "intervals": {},
    }
    if len(days) < 2 or replicates <= 0:
        return result

    rng = random.Random(seed)
    samples: dict[str, list[float]] = {
        "accuracy": [],
        "brier_score": [],
        "log_loss": [],
        "ece": [],
    }
    for _ in range(replicates):
        sampled_predictions = [
            prediction for day in (rng.choice(days) for _ in days) for prediction in grouped[day]
        ]
        pairs = [
            (prediction.probability_up, prediction.outcome_up) for prediction in sampled_predictions
        ]
        metrics = _metrics(pairs)
        bucket_rows = _bucket_rows(
            sampled_predictions,
            PROBABILITY_BUCKETS,
            confidence=False,
            pnl_available=False,
        )
        ece = sum(float(row["ece_contribution"] or 0.0) for row in bucket_rows) / len(
            sampled_predictions
        )
        for metric in ("accuracy", "brier_score", "log_loss"):
            samples[metric].append(float(metrics[metric]))
        samples["ece"].append(ece)
    result["intervals"] = {
        metric: {
            "lower_95": _percentile(sorted(values), 0.025),
            "upper_95": _percentile(sorted(values), 0.975),
        }
        for metric, values in samples.items()
    }
    return result


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def analyze_calibration(
    input_path: str | Path,
    bins_output_path: str | Path,
    daily_output_path: str | Path,
    report_path: str | Path,
    *,
    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, object]:
    """Analyze a detailed backtest CSV and write calibration tables and report."""

    if bootstrap_replicates < 0:
        raise ValueError("bootstrap replicates cannot be negative")
    input_path = Path(input_path)
    bins_output_path = Path(bins_output_path)
    daily_output_path = Path(daily_output_path)
    report_path = Path(report_path)
    predictions, coverage = _load_predictions(input_path)
    pnl_available = bool(coverage["pnl_data_available"])

    probability_rows = _bucket_rows(
        predictions, PROBABILITY_BUCKETS, confidence=False, pnl_available=pnl_available
    )
    confidence_rows = _bucket_rows(
        predictions, CONFIDENCE_BUCKETS, confidence=True, pnl_available=pnl_available
    )
    table_rows = probability_rows + confidence_rows
    daily_rows, walk_forward_predictions = _daily_rows(predictions, pnl_available=pnl_available)
    _write_csv(bins_output_path, TABLE_FIELDS, table_rows)
    _write_csv(daily_output_path, DAILY_FIELDS, daily_rows)

    overall = _metrics(
        (prediction.probability_up, prediction.outcome_up) for prediction in predictions
    )
    baseline_half = _baseline_metrics(predictions, 0.5)
    walk_forward_oracle = _metrics(
        (prediction.probability_up, prediction.outcome_up)
        for prediction in walk_forward_predictions
    )
    predictions_by_day = _group_predictions(predictions)
    baseline_losses: list[float] = []
    for row in daily_rows:
        prevalence = row["walk_forward_prevalence_up"]
        if prevalence is None:
            continue
        day_values = predictions_by_day[row["utc_day"]]
        baseline_losses.extend(
            (float(prevalence) - prediction.outcome_up) ** 2 for prediction in day_values
        )
    baseline_brier = sum(baseline_losses) / len(baseline_losses) if baseline_losses else None
    skill = (
        1.0 - float(walk_forward_oracle["brier_score"]) / baseline_brier
        if baseline_brier is not None and baseline_brier > 0.0
        else None
    )
    ece = (
        sum(float(row["ece_contribution"] or 0.0) for row in probability_rows) / len(predictions)
        if predictions
        else None
    )
    pnl = _pnl_summary(predictions, available=pnl_available)
    report: dict[str, object] = {
        "input_file": str(input_path),
        "bins_output_file": str(bins_output_path),
        "daily_output_file": str(daily_output_path),
        "label_source": "outcome_polymarket",
        "probability_source": "prob_up from the Oracle backtest",
        "coverage": coverage,
        "overall": overall,
        "baseline_constant_0_5": baseline_half,
        "walk_forward_prevalence_baseline": {
            "evaluation_n": len(walk_forward_predictions),
            "baseline_brier_score": baseline_brier,
            "oracle_brier_score": walk_forward_oracle["brier_score"],
            "brier_skill_score": skill,
            "days_without_prior_training_data": sum(
                row["walk_forward_prevalence_up"] is None for row in daily_rows
            ),
        },
        "reliability": {
            "definition": "weighted mean absolute calibration gap across probability_up buckets",
            "ece": ece,
            "buckets": [
                {
                    "bucket": row["bucket"],
                    "n": row["n"],
                    "predicted_mean": row["predicted_mean"],
                    "observed_rate": row["observed_rate"],
                    "calibration_gap": row["calibration_gap"],
                }
                for row in probability_rows
            ],
        },
        "pnl_synthetic": {
            "available": pnl_available,
            **pnl,
            "interpretation": (
                "hypothetical only; the entry price is the Oracle fair value, not an "
                "executable historical Polymarket price"
                if pnl_available
                else "unavailable; the input CSV does not contain the P&L columns"
            ),
            "uncertain_price_band": [UNCERTAIN_BAND_LOW, UNCERTAIN_BAND_HIGH],
        },
        "uncertainty": _bootstrap_intervals(
            predictions, replicates=bootstrap_replicates, seed=bootstrap_seed
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def _group_predictions(predictions: list[Prediction]) -> dict[str, list[Prediction]]:
    grouped: dict[str, list[Prediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[prediction.day].append(prediction)
    return grouped


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze calibration of Oracle backtest results")
    parser.add_argument("--input", type=Path, default=Path("data/backtesting/backtest.csv"))
    parser.add_argument(
        "--bins-output", type=Path, default=Path("data/backtesting/calibration-bins.csv")
    )
    parser.add_argument(
        "--daily-output", type=Path, default=Path("data/backtesting/calibration-daily.csv")
    )
    parser.add_argument("--report", type=Path, default=Path("data/backtesting/calibration.json"))
    parser.add_argument("--bootstrap-replicates", type=int, default=DEFAULT_BOOTSTRAP_REPLICATES)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = analyze_calibration(
            args.input,
            args.bins_output,
            args.daily_output,
            args.report,
            bootstrap_replicates=args.bootstrap_replicates,
            bootstrap_seed=args.bootstrap_seed,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    overall = report["overall"]
    print(
        f"Analyzed {overall['n']} scored markets; Brier={overall['brier_score']}, "
        f"log_loss={overall['log_loss']}, ECE={report['reliability']['ece']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
