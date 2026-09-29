import csv
import json

import pytest

from arbitrage_poly.apps.calibration import analyze_calibration

FIELDS = (
    "market_id",
    "window_start_utc",
    "prob_up",
    "outcome_polymarket",
    "scored",
    "exclusion_reason",
    "pnl_exclusion_reason",
    "pnl_usd",
    "stake_usd",
)


def _write_backtest(path, rows, *, fields=FIELDS) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row(market_id, day, probability, outcome, *, pnl="", pnl_reason=""):
    return {
        "market_id": market_id,
        "window_start_utc": f"2026-01-{day:02d}T00:00:00Z",
        "prob_up": str(probability),
        "outcome_polymarket": outcome,
        "scored": "True",
        "exclusion_reason": "",
        "pnl_exclusion_reason": pnl_reason,
        "pnl_usd": pnl,
        "stake_usd": "1.0" if pnl else "",
    }


def test_calibration_analysis_scores_probability_and_confidence_bins(tmp_path) -> None:
    source = tmp_path / "backtest.csv"
    bins_path = tmp_path / "bins.csv"
    daily_path = tmp_path / "daily.csv"
    report_path = tmp_path / "report.json"
    _write_backtest(
        source,
        [
            _row("up-win", 1, 0.8, "UP", pnl="0.25"),
            _row("up-loss", 1, 0.8, "DOWN", pnl="-1.0"),
            _row("down-win", 2, 0.2, "DOWN", pnl="0.25"),
            _row("neutral", 2, 0.5, "UP", pnl_reason="uncertain_price_band"),
        ],
    )

    report = analyze_calibration(
        source, bins_path, daily_path, report_path, bootstrap_replicates=20
    )

    assert report["overall"]["n"] == 4
    assert report["overall"]["accuracy"] == pytest.approx(0.75)
    assert report["overall"]["brier_score"] == pytest.approx(0.2425)
    assert report["pnl_synthetic"]["pnl_bets"] == 3
    assert report["pnl_synthetic"]["pnl_excluded_uncertain"] == 1
    assert report["uncertainty"]["available_blocks"] == 2
    assert report["uncertainty"]["exploratory"] is False

    with bins_path.open(newline="", encoding="utf-8") as input_file:
        bins = list(csv.DictReader(input_file))
    confidence_high = next(
        row for row in bins if row["group_type"] == "confidence" and row["bucket"] == "(0.75,0.85]"
    )
    assert confidence_high["n"] == "3"
    assert float(confidence_high["observed_rate"]) == pytest.approx(2 / 3)
    assert confidence_high["accuracy"] == ""
    assert confidence_high["pnl_bets"] == "3"
    middle = next(
        row
        for row in bins
        if row["group_type"] == "probability_up" and row["bucket"] == "[0.50,0.55]"
    )
    assert middle["n"] == "1"
    assert middle["pnl_excluded_uncertain"] == "1"

    with daily_path.open(newline="", encoding="utf-8") as input_file:
        daily = list(csv.DictReader(input_file))
    assert daily[0]["walk_forward_train_n"] == "0"
    assert daily[1]["walk_forward_train_n"] == "2"


def test_calibration_analysis_validates_duplicate_market_ids(tmp_path) -> None:
    source = tmp_path / "backtest.csv"
    _write_backtest(source, [_row("duplicate", 1, 0.7, "UP"), _row("duplicate", 2, 0.3, "DOWN")])

    with pytest.raises(ValueError, match="duplicate market_id"):
        analyze_calibration(
            source, tmp_path / "bins.csv", tmp_path / "daily.csv", tmp_path / "report.json"
        )


def test_calibration_analysis_counts_unscored_exclusions(tmp_path) -> None:
    source = tmp_path / "backtest.csv"
    row = _row("missing", 1, 0.0, "", pnl_reason="")
    row.update(scored="False", exclusion_reason="no_prediction_before_cutoff")
    _write_backtest(source, [row])

    report = analyze_calibration(
        source,
        tmp_path / "bins.csv",
        tmp_path / "daily.csv",
        tmp_path / "report.json",
        bootstrap_replicates=0,
    )

    assert report["coverage"]["excluded_rows_by_reason"] == {"no_prediction_before_cutoff": 1}
    assert report["overall"]["n"] == 0
    assert report["uncertainty"]["exploratory"] is True
    assert json.loads((tmp_path / "report.json").read_text())["overall"]["n"] == 0


def test_calibration_analysis_accepts_legacy_csv_without_pnl_columns(tmp_path) -> None:
    source = tmp_path / "backtest.csv"
    legacy_fields = tuple(
        field for field in FIELDS if field not in {"pnl_exclusion_reason", "pnl_usd", "stake_usd"}
    )
    legacy_row = _row("legacy", 1, 0.8, "UP")
    for field in {"pnl_exclusion_reason", "pnl_usd", "stake_usd"}:
        legacy_row.pop(field)
    _write_backtest(source, [legacy_row], fields=legacy_fields)

    report = analyze_calibration(
        source,
        tmp_path / "bins.csv",
        tmp_path / "daily.csv",
        tmp_path / "report.json",
        bootstrap_replicates=0,
    )

    assert report["overall"]["n"] == 1
    assert report["coverage"]["pnl_data_available"] is False
    assert report["pnl_synthetic"]["available"] is False
    assert report["pnl_synthetic"]["total_pnl_usd"] is None
