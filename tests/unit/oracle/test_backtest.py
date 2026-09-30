import csv
import json
from datetime import UTC, datetime, timedelta

import pytest

from arbitrage_poly.apps.backtest import _is_uncertain_price, last_days_start_ns, run_backtest


def _write_market(path, end_date: datetime, outcome_up: str = "1") -> None:
    outcome_down = "0" if outcome_up == "1" else "1"
    market = {
        "slug": "btc-updown-test",
        "market_id": "test-market",
        "endDate": end_date.isoformat().replace("+00:00", "Z"),
        "closed": True,
        "resolution_status": "resolved",
        "outcome_up": outcome_up,
        "outcome_down": outcome_down,
    }
    path.write_text(json.dumps([market]), encoding="utf-8")


def _write_aggtrades(path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file)
        for trade_id, timestamp, price in rows:
            writer.writerow(
                [trade_id, price, 1, trade_id, trade_id, timestamp, "False", "True"]
            )


def _read_output(path):
    with path.open(newline="", encoding="utf-8") as input_file:
        return next(csv.DictReader(input_file))


def test_backtest_uses_only_window_ticks_and_cutoff_tick(tmp_path) -> None:
    end_date = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    end_us = int(end_date.timestamp() * 1_000_000)
    start_us = end_us - 300_000_000
    cutoff_us = end_us - 120_000_000
    markets_path = tmp_path / "markets.json"
    trades_path = tmp_path / "trades.csv"
    output_path = tmp_path / "backtest.csv"
    report_path = tmp_path / "report.json"
    _write_market(markets_path, end_date)
    _write_aggtrades(
        trades_path,
        [
            (1, start_us - 1_000_000, 1),
            (2, start_us, 100),
            (3, start_us + 1_000_000, 101),
            (4, cutoff_us, 102),
            (5, cutoff_us + 1_000_000, 50),
            (6, end_us, 999),
        ],
    )

    report = run_backtest(
        markets_path, trades_path, output_path, report_path, volatility_model="tick_ewma"
    )

    row = _read_output(output_path)
    assert row["reference_price"] == "100.0"
    assert row["current_price"] == "102.0"
    assert row["fair_value_ts_utc"] == "2026-01-01T00:03:00.000000Z"
    assert row["outcome_binance"] == "DOWN"
    assert row["outcome_polymarket"] == "UP"
    assert row["scored"] == "True"
    assert report["scored_windows"] == 1
    assert report["binance_polymarket_outcome_mismatches"] == 1
    assert report["ticks_in_market_windows"] == 4
    assert (report["probability_floor"], report["probability_ceiling"]) == (0.001, 0.999)


def test_backtest_resets_oracle_volatility_for_each_market(tmp_path) -> None:
    first_end = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    second_end = first_end + timedelta(minutes=5)
    end_us = int(second_end.timestamp() * 1_000_000)
    start_us = end_us - 300_000_000
    cutoff_us = end_us - 120_000_000
    markets_path = tmp_path / "markets.json"
    trades_path = tmp_path / "trades.csv"
    output_path = tmp_path / "backtest.csv"
    report_path = tmp_path / "report.json"
    first_market = {
        "slug": "first",
        "market_id": "first",
        "endDate": first_end.isoformat().replace("+00:00", "Z"),
        "resolution_status": "resolved",
        "outcome_up": "1",
        "outcome_down": "0",
    }
    second_market = {
        "slug": "second",
        "market_id": "second",
        "endDate": second_end.isoformat().replace("+00:00", "Z"),
        "resolution_status": "resolved",
        "outcome_up": "1",
        "outcome_down": "0",
    }
    markets_path.write_text(json.dumps([first_market, second_market]), encoding="utf-8")
    _write_aggtrades(
        trades_path,
        [
            (1, start_us - 300_000_000, 1),
            (2, start_us - 60_000_000, 2),
            (3, start_us, 100),
            (4, start_us + 1_000_000, 100),
            (5, cutoff_us, 101),
        ],
    )

    run_backtest(
        markets_path, trades_path, output_path, report_path, volatility_model="tick_ewma"
    )

    with output_path.open(newline="", encoding="utf-8") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[1]["reference_price"] == "100.0"
    assert rows[1]["current_price"] == "101.0"
    assert rows[1]["fair_value_ts_utc"] == "2026-01-01T00:08:00.000000Z"
    assert rows[0]["volatility"] != rows[1]["volatility"]


def test_backtest_calculates_one_dollar_oracle_fair_value_pnl(tmp_path) -> None:
    first_end = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    second_end = first_end + timedelta(minutes=5)
    markets_path = tmp_path / "markets.json"
    trades_path = tmp_path / "trades.csv"
    output_path = tmp_path / "backtest.csv"
    report_path = tmp_path / "report.json"
    markets = []
    for market_id, end_date, up_outcome in (
        ("winner", first_end, "1"),
        ("loser", second_end, "0"),
    ):
        markets.append(
            {
                "slug": market_id,
                "market_id": market_id,
                "endDate": end_date.isoformat().replace("+00:00", "Z"),
                "resolution_status": "resolved",
                "outcome_up": up_outcome,
                "outcome_down": "0" if up_outcome == "1" else "1",
            }
        )
    markets_path.write_text(json.dumps(markets), encoding="utf-8")

    first_start_us = int(first_end.timestamp() * 1_000_000) - 300_000_000
    second_start_us = int(second_end.timestamp() * 1_000_000) - 300_000_000
    _write_aggtrades(
        trades_path,
        [
            (1, first_start_us, 100),
            (2, first_start_us + 180_000_000, 101),
            (3, second_start_us, 100),
            (4, second_start_us + 180_000_000, 101),
        ],
    )

    report = run_backtest(
        markets_path,
        trades_path,
        output_path,
        report_path,
        probability_floor=0.05,
        probability_ceiling=0.95,
        volatility_model="tick_ewma",
    )

    with output_path.open(newline="", encoding="utf-8") as input_file:
        rows = list(csv.DictReader(input_file))
    assert [row["pnl_side"] for row in rows] == ["UP", "UP"]
    assert [float(row["entry_price"]) for row in rows] == [0.95, 0.95]
    assert [float(row["shares"]) for row in rows] == pytest.approx([1 / 0.95, 1 / 0.95])
    assert [float(row["payout_usd"]) for row in rows] == pytest.approx([1 / 0.95, 0.0])
    assert [float(row["pnl_usd"]) for row in rows] == pytest.approx([1 / 0.95 - 1, -1.0])
    assert [float(row["cumulative_pnl_usd"]) for row in rows] == pytest.approx(
        [1 / 0.95 - 1, 1 / 0.95 - 2]
    )
    assert report["pnl_markets"] == 2
    assert report["pnl_wins"] == 1
    assert report["pnl_losses"] == 1
    assert report["total_staked_usd"] == 2.0
    assert report["total_payout_usd"] == pytest.approx(1 / 0.95)
    assert report["total_pnl_usd"] == pytest.approx(1 / 0.95 - 2)
    assert report["roi"] == pytest.approx((1 / 0.95 - 2) / 2)
    assert report["max_drawdown_usd"] == -1.0


@pytest.mark.parametrize("price", [0.45, 0.50, 0.55])
def test_uncertain_price_band_includes_both_bounds(price: float) -> None:
    assert _is_uncertain_price(price)


@pytest.mark.parametrize("price", [0.449999, 0.550001])
def test_uncertain_price_band_excludes_values_outside_bounds(price: float) -> None:
    assert not _is_uncertain_price(price)


def test_uncertain_price_skips_pnl_without_skipping_oracle_score(tmp_path) -> None:
    end_date = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    end_us = int(end_date.timestamp() * 1_000_000)
    start_us = end_us - 300_000_000
    markets_path = tmp_path / "markets.json"
    trades_path = tmp_path / "trades.csv"
    output_path = tmp_path / "backtest.csv"
    report_path = tmp_path / "report.json"
    _write_market(markets_path, end_date)
    _write_aggtrades(
        trades_path,
        [
            (1, start_us, 100),
            (2, start_us + 240_000_000, 100),
        ],
    )

    report = run_backtest(
        markets_path, trades_path, output_path, report_path, volatility_model="tick_ewma"
    )

    row = _read_output(output_path)
    assert row["scored"] == "True"
    assert row["pnl_side"] == "UP"
    assert row["entry_price"] == "0.5"
    assert row["pnl_exclusion_reason"] == "uncertain_price_band"
    assert row["stake_usd"] == ""
    assert row["pnl_usd"] == ""
    assert report["scored_windows"] == 1
    assert report["pnl_markets"] == 0
    assert report["pnl_excluded_by_reason"] == {"uncertain_price_band": 1}
    assert report["total_staked_usd"] == 0.0
    assert report["total_pnl_usd"] == 0.0
    assert report["roi"] is None


def test_last_days_start_counts_back_from_latest_market_end(tmp_path) -> None:
    markets_path = tmp_path / "markets.json"
    ends = [datetime(2026, 1, day, 12, 5, tzinfo=UTC) for day in (1, 2, 3)]
    markets_path.write_text(
        json.dumps(
            [
                {"market_id": str(i), "endDate": end.isoformat().replace("+00:00", "Z")}
                for i, end in enumerate(reversed(ends))
            ]
        ),
        encoding="utf-8",
    )

    start_ns = last_days_start_ns(markets_path, 1)

    expected = ends[-1] - timedelta(days=1)
    assert start_ns == int(expected.timestamp()) * 1_000_000_000
    with pytest.raises(ValueError):
        last_days_start_ns(markets_path, 0)


def test_horizon_volatility_is_continuous_and_warms_up_before_scoring(tmp_path) -> None:
    end_date = datetime(2026, 1, 1, 2, 5, tzinfo=UTC)
    end_us = int(end_date.timestamp() * 1_000_000)
    start_us = end_us - 300_000_000
    markets_path = tmp_path / "markets.json"
    trades_path = tmp_path / "trades.csv"
    _write_market(markets_path, end_date)
    # Two hours of alternating prices before the market only feed the volatility.
    history_start_us = start_us - 2 * 3_600_000_000
    rows = [
        (i, history_start_us + i * 1_000_000, 100.0 if i % 120 < 60 else 100.1)
        for i in range(7_200)
    ]
    rows += [(7_200, start_us, 100.0), (7_201, end_us - 61_000_000, 100.05)]
    _write_aggtrades(trades_path, rows)

    report = run_backtest(markets_path, trades_path, tmp_path / "o.csv", tmp_path / "r.json")

    row = _read_output(tmp_path / "o.csv")
    assert row["scored"] == "True"
    assert row["reference_price"] == "100.0"
    assert float(row["volatility"]) > 0.01
    assert report["volatility_model"] == "horizon_ewma"

    short_trades = tmp_path / "short.csv"
    _write_aggtrades(short_trades, rows[-600:])
    report = run_backtest(markets_path, short_trades, tmp_path / "o2.csv", tmp_path / "r2.json")
    assert report["excluded_windows_by_reason"] == {"volatility_warmup": 1}
