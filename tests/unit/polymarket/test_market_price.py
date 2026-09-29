import csv
import json
from datetime import UTC, datetime

import pytest

from arbitrage_poly.apps.market_price import analyze_market_price


def _write_backtest(path, *, offset_seconds: int = 60) -> None:
    end = datetime(2026, 9, 22, 22, 40, tzinfo=UTC)
    cutoff = end.timestamp() - offset_seconds
    row = {
        "market_id": "4824328",
        "slug": "btc-updown-5m-1790116500",
        "window_start_utc": "2026-09-22T22:35:00Z",
        "end_date_utc": end.isoformat().replace("+00:00", "Z"),
        "prediction_cutoff_utc": datetime.fromtimestamp(cutoff, UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "fair_value_ts_utc": "2026-09-22T22:38:57.877262Z",
        "prob_up": "0.05675450520522152",
        "prob_down": "0.9432454947947785",
    }
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=row.keys())
        writer.writeheader()
        writer.writerow(row)


def _write_trades(path, trades) -> None:
    fields = ("slug", "timestamp", "outcome", "side", "price", "size")
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        for timestamp, outcome, side, price, size in trades:
            writer.writerow(
                {
                    "slug": "btc-updown-5m-1790116500",
                    "timestamp": timestamp,
                    "outcome": outcome,
                    "side": side,
                    "price": price,
                    "size": size,
                }
            )


def test_market_price_uses_only_trades_at_or_before_t60(tmp_path) -> None:
    backtest_path = tmp_path / "backtest.csv"
    trades_path = tmp_path / "trades.csv"
    output_path = tmp_path / "price.json"
    _write_backtest(backtest_path)
    cutoff = datetime(2026, 9, 22, 22, 39, tzinfo=UTC).timestamp()
    _write_trades(
        trades_path,
        [
            (cutoff - 10, "Up", "BUY", 0.10, 10),
            (cutoff - 5, "Up", "SELL", 0.20, 10),
            (cutoff, "Down", "BUY", 0.90, 20),
            (cutoff + 1, "Up", "BUY", 0.99, 1),
        ],
    )

    report = analyze_market_price(
        "4824328",
        trades_path,
        backtest_path,
        output_path,
        windows_seconds=(5, 15),
        primary_window_seconds=15,
    )

    up_latest = report["prices_by_outcome"]["UP"]["latest_trade"]
    down_latest = report["prices_by_outcome"]["DOWN"]["latest_trade"]
    assert up_latest["price"] == 0.2
    assert up_latest["age_seconds_at_cutoff"] == 5
    assert down_latest["price"] == 0.9
    assert report["trade_data"]["trades_after_cutoff_ignored"] == 1
    assert report["primary_estimate"]["probability_up_from_up_vwap"] == pytest.approx(0.15)
    assert report["primary_estimate"]["probability_up_from_down_vwap"] == pytest.approx(0.1)
    assert report["primary_estimate"]["estimated_probability_up"] == pytest.approx(0.125)
    assert report["primary_estimate"]["difference_vs_oracle"] == pytest.approx(
        0.125 - 0.05675450520522152
    )
    assert report["trade_data"]["executable_quote_available"] is False
    assert json.loads(output_path.read_text())["market_id"] == "4824328"


def test_market_price_rejects_backtest_with_wrong_offset(tmp_path) -> None:
    backtest_path = tmp_path / "backtest.csv"
    trades_path = tmp_path / "trades.csv"
    _write_backtest(backtest_path, offset_seconds=120)
    _write_trades(trades_path, [])

    with pytest.raises(ValueError, match="expected T-60s"):
        analyze_market_price(
            "4824328",
            trades_path,
            backtest_path,
            tmp_path / "price.json",
        )


def test_market_price_rejects_trade_file_for_different_slug(tmp_path) -> None:
    backtest_path = tmp_path / "backtest.csv"
    trades_path = tmp_path / "trades.csv"
    _write_backtest(backtest_path)
    trades_path.write_text(
        "slug,timestamp,outcome,side,price,size\n"
        "btc-updown-5m-other,1790116739,Up,BUY,0.1,1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="slug does not match"):
        analyze_market_price(
            "4824328",
            trades_path,
            backtest_path,
            tmp_path / "price.json",
        )