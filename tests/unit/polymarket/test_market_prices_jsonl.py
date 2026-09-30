import csv
import json
from datetime import UTC, datetime

import pytest

from arbitrage_poly.apps.market_prices_jsonl import analyze_jsonl_market_prices


def _write_backtest(path, markets, *, offset_seconds: int = 120) -> None:
    fields = (
        "market_id",
        "slug",
        "end_date_utc",
        "prediction_cutoff_utc",
        "prob_up",
        "prob_down",
    )
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        for market_id, slug, end, prob_up, prob_down in markets:
            end_dt = datetime.fromtimestamp(end, UTC)
            cutoff_dt = datetime.fromtimestamp(end - offset_seconds, UTC)
            writer.writerow(
                {
                    "market_id": market_id,
                    "slug": slug,
                    "end_date_utc": end_dt.isoformat().replace("+00:00", "Z"),
                    "prediction_cutoff_utc": cutoff_dt.isoformat().replace("+00:00", "Z"),
                    "prob_up": prob_up,
                    "prob_down": prob_down,
                }
            )


def _write_jsonl(path, trades) -> None:
    with path.open("w", encoding="utf-8") as output_file:
        for timestamp, slug, outcome, side, price, size in trades:
            output_file.write(
                json.dumps(
                    {
                        "timestamp": timestamp,
                        "slug": slug,
                        "outcome": outcome,
                        "side": side,
                        "price": price,
                        "size": size,
                    }
                )
                + "\n"
            )


def test_jsonl_prices_stream_sorted_trades_and_stop_after_latest_cutoff(tmp_path) -> None:
    backtest_path = tmp_path / "backtest.csv"
    trades_path = tmp_path / "trades.jsonl"
    output_path = tmp_path / "prices.json"
    cutoff_one = datetime(2026, 1, 1, 0, 4, tzinfo=UTC).timestamp()
    cutoff_two = datetime(2026, 1, 1, 0, 9, tzinfo=UTC).timestamp()
    _write_backtest(
        backtest_path,
        [
            ("market-1", "market-one", cutoff_one + 120, "0.4", "0.6"),
            ("market-2", "aaa-market", cutoff_two + 120, "0.7", "0.3"),
        ],
    )
    _write_jsonl(
        trades_path,
        [
            (cutoff_one + 1, "market-one", "Up", "BUY", 0.99, 10),
            (cutoff_one, "market-one", "Down", "SELL", 0.80, 10),
            (cutoff_one - 5, "market-one", "Up", "BUY", 0.30, 10),
            (cutoff_one - 15, "market-one", "Up", "BUY", 0.20, 10),
            (cutoff_two + 1, "aaa-market", "Down", "SELL", 0.01, 10),
            (cutoff_two - 5, "aaa-market", "Down", "SELL", 0.25, 15),
            (cutoff_two - 10, "aaa-market", "Up", "BUY", 0.70, 5),
            (cutoff_two, "market-z", "Up", "BUY", 0.50, 1),
        ],
    )

    report = analyze_jsonl_market_prices(trades_path, backtest_path, output_path, window_seconds=15)

    first, second = report["markets"]
    assert first["market_id"] == "market-1"
    assert first["price_up"] == pytest.approx(0.3)
    assert first["price_down"] == pytest.approx(0.8)
    assert first["up_trade_count"] == 1
    assert first["last_up_trade"]["price"] == 0.3
    assert second["market_id"] == "market-2"
    assert second["price_up"] == pytest.approx(0.7)
    assert second["price_down"] == pytest.approx(0.25)
    assert report["input"]["markets_past_price_window_boundary"] == 1
    assert report["input"]["lines_scanned"] == 8
    assert report["cutoff_offset_seconds"] == 120
    assert json.loads(output_path.read_text())["markets"][0]["market_id"] == "market-1"

def test_jsonl_prices_supports_a_matching_nondefault_offset(tmp_path) -> None:
    backtest_path = tmp_path / "backtest.csv"
    trades_path = tmp_path / "trades.jsonl"
    output_path = tmp_path / "prices.json"
    _write_backtest(
        backtest_path,
        [("market-1", "market-one", 1_800_000_000, "0.4", "0.6")],
        offset_seconds=60,
    )
    _write_jsonl(trades_path, [])

    report = analyze_jsonl_market_prices(
        trades_path, backtest_path, output_path, prediction_offset_s=60
    )

    assert report["cutoff_offset_seconds"] == 60
    assert report["markets"][0]["price_method"].endswith("T-60s]")


def test_jsonl_prices_reject_unsorted_timestamps(tmp_path) -> None:
    backtest_path = tmp_path / "backtest.csv"
    trades_path = tmp_path / "trades.jsonl"
    _write_backtest(
        backtest_path,
        [("market-1", "market-one", 1_800_000_000, "0.4", "0.6")],
    )
    _write_jsonl(
        trades_path,
        [
            (1_799_999_929, "market-one", "Up", "BUY", 0.3, 1),
            (1_799_999_930, "market-one", "Up", "BUY", 0.2, 1),
        ],
    )

    with pytest.raises(ValueError, match="not reverse-sorted"):
        analyze_jsonl_market_prices(
            trades_path, backtest_path, tmp_path / "prices.json", window_seconds=15
        )


def test_jsonl_prices_reject_backtest_not_at_t120(tmp_path) -> None:
    backtest_path = tmp_path / "backtest.csv"
    trades_path = tmp_path / "trades.jsonl"
    _write_backtest(
        backtest_path,
        [("market-1", "market-one", 1_800_000_000, "0.4", "0.6")],
    )
    _write_jsonl(trades_path, [])
    text = backtest_path.read_text(encoding="utf-8")
    text = text.replace("2027-01-15T07:58:00Z", "2027-01-15T07:59:00Z")
    backtest_path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="expected T-120s"):
        analyze_jsonl_market_prices(
            trades_path, backtest_path, tmp_path / "prices.json", window_seconds=15
        )


def test_jsonl_prices_skips_trade_fields_outside_price_window(tmp_path) -> None:
    backtest_path = tmp_path / "backtest.csv"
    trades_path = tmp_path / "trades.jsonl"
    cutoff = 1_799_999_940
    _write_backtest(
        backtest_path,
        [("market-1", "market-one", cutoff + 120, "0.4", "0.6")],
    )
    trades_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "timestamp": cutoff + 1,
                        "slug": "market-one",
                        "outcome": "INVALID",
                        "side": "INVALID",
                        "price": "bad",
                        "size": "bad",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": cutoff - 1,
                        "slug": "market-one",
                        "outcome": "Up",
                        "side": "BUY",
                        "price": 0.4,
                        "size": 2,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": cutoff - 16,
                        "slug": "market-one",
                        "outcome": "INVALID",
                        "side": "INVALID",
                        "price": "bad",
                        "size": "bad",
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    report = analyze_jsonl_market_prices(
        trades_path, backtest_path, tmp_path / "prices.json", window_seconds=15
    )

    assert report["markets"][0]["price_up"] == pytest.approx(0.4)
    assert report["markets"][0]["up_trade_count"] == 1
    assert report["input"]["priced_trades_validated"] == 1
