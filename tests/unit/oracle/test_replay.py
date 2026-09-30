import csv

import pytest

from arbitrage_poly.apps.replay import run_replay
from arbitrage_poly.pricing import SizingConfig, compute_sizing

FIELDS = [
    "ts_ns",
    "exchange_ts_ns",
    "received_ts_ns",
    "price",
    "qty",
    "source",
    "seq",
]


def write_row(writer: csv.DictWriter, ts_ns: str, price: str, source: str) -> None:
    writer.writerow(
        {
            "ts_ns": ts_ns,
            "exchange_ts_ns": ts_ns,
            "received_ts_ns": ts_ns,
            "price": price,
            "qty": "1",
            "source": source,
            "seq": "1",
        }
    )


def test_replay_classifies_windows_and_reports_invalid_rows(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    report_path = tmp_path / "report.json"
    with input_path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        write_row(writer, "10", "100", "binance.book_ticker")
        write_row(writer, "11", "101", "binance.book_ticker")
        write_row(writer, "19", "102", "binance.book_ticker")
        write_row(writer, "20", "102", "binance.book_ticker")
        write_row(writer, "21", "102", "binance.agg_trade")
        writer.writerow(
            {
                "ts_ns": "bad",
                "exchange_ts_ns": "22",
                "received_ts_ns": "22",
                "price": "103",
                "qty": "1",
                "source": "binance.book_ticker",
                "seq": "2",
            }
        )

    report = run_replay(input_path, output_path, report_path, window_ns=10, prediction_offset_ns=1)

    assert report.rows_read == 6
    assert report.ticks_accepted == 4
    assert report.rejected_rows == [7]
    assert report.ignored_sources == {"binance.agg_trade": 1}
    assert report.complete_windows == 1
    assert report.partial_windows == 1
    # With a tiny window_ns=10, prediction_offset_ns must be smaller than the
    # window itself; here it lands on the tick at ts=19, so the one complete
    # window is scored (unlike the module default of 120s, which would exceed
    # window_ns and never select a prediction).
    assert report.scored_windows == 1
    assert report.brier_score is not None
    assert report.log_loss is not None

    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.DictReader(output_file))
    assert rows[0]["window_status"] == "complete"
    assert rows[0]["outcome"] == "UP"
    assert rows[-1]["window_status"] == "partial"
    assert report_path.exists()


def test_replay_reads_native_aggtrades_format(tmp_path) -> None:
    input_path = tmp_path / "aggtrades.csv"
    output_path = tmp_path / "fair_values.csv"
    with input_path.open("w", encoding="utf-8") as input_file:
        input_file.write("1,100,1,1,1,10,False,True\n")
        input_file.write("2,101,1,2,2,19,False,True\n")
        input_file.write("3,101,1,3,3,20,False,True\n")
        input_file.write("4,102,1,4,4,29,False,True\n")

    report = run_replay(
        input_path,
        output_path,
        input_format="aggtrades",
        window_ns=10_000,
        prediction_offset_ns=1,
    )

    assert report.rows_read == 4
    assert report.ticks_accepted == 4
    assert report.complete_windows == 2
    assert report.partial_windows == 0
    assert report.rejected_rows == []
    assert report.volatility_sampling_interval_ns == 1_000_000_000
    assert report.probability_floor == 0.05
    assert report.probability_ceiling == 0.95


def test_replay_scores_one_prediction_at_t_minus_120_seconds_by_default(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    with input_path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        write_row(writer, "1", "100", "binance.book_ticker")
        write_row(writer, "179_000_000_000", "101", "binance.book_ticker")
        write_row(writer, "180_000_000_000", "101", "binance.book_ticker")
        write_row(writer, "299_000_000_000", "102", "binance.book_ticker")
        write_row(writer, "300_000_000_000", "102", "binance.book_ticker")

    report = run_replay(
        input_path,
        output_path,
        window_ns=300_000_000_000,
    )

    assert report.complete_windows == 1
    assert report.scored_windows == 1
    assert report.prediction_offset_ns == 120_000_000_000


def test_replay_reports_fixed_stake_pnl(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    with input_path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        write_row(writer, "1", "100", "binance.book_ticker")
        write_row(writer, "59_000_000_000", "101", "binance.book_ticker")
        write_row(writer, "119_000_000_000", "102", "binance.book_ticker")
        write_row(writer, "120_000_000_000", "102", "binance.book_ticker")

    report = run_replay(
        input_path,
        output_path,
        window_ns=120_000_000_000,
        prediction_offset_ns=60_000_000_000,
        entry_price=0.4,
        stake_usd=10.0,
    )

    assert report.pnl_enabled is True
    assert report.pnl_windows == 1
    assert report.total_staked_usd == 10.0
    assert report.total_payout_usd == 25.0
    assert report.total_pnl_usd == 15.0
    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.DictReader(output_file))
    pnl_rows = [row for row in rows if row["pnl_usd"]]
    assert len(pnl_rows) == 1
    assert pnl_rows[0]["prediction_ts_ns"] == "59000000000"
    assert len({row["window_start_ns"] for row in pnl_rows}) == len(pnl_rows)
    assert float(pnl_rows[0]["shares"]) == 25.0
    assert float(pnl_rows[0]["cumulative_pnl_usd"]) == 15.0


def test_replay_skips_bet_when_fair_value_does_not_exceed_entry_price(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    with input_path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        write_row(writer, "1", "100", "binance.book_ticker")
        write_row(writer, "59_000_000_000", "101", "binance.book_ticker")
        write_row(writer, "119_000_000_000", "102", "binance.book_ticker")
        write_row(writer, "120_000_000_000", "102", "binance.book_ticker")

    report = run_replay(
        input_path,
        output_path,
        window_ns=120_000_000_000,
        prediction_offset_ns=60_000_000_000,
        entry_price=0.99,
        stake_usd=10.0,
    )

    assert report.pnl_enabled is True
    assert report.pnl_windows == 0


def test_replay_dynamic_sizing_uses_kelly_stake_and_exports_kelly_fraction(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    with input_path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        write_row(writer, "1", "100", "binance.book_ticker")
        write_row(writer, "59_000_000_000", "101", "binance.book_ticker")
        write_row(writer, "119_000_000_000", "102", "binance.book_ticker")
        write_row(writer, "120_000_000_000", "102", "binance.book_ticker")

    sizing_config = SizingConfig(mode="dynamic", bankroll=10.0, kelly_fraction_cap=0.5)
    # This window's prob_up at T-120s is 0.95 (verified separately); the entry
    # price is the synthetic constant below.
    expected = compute_sizing(accepted=True, config=sizing_config, probability=0.95, price=0.4)
    assert expected.stake_usd > 0.0

    report = run_replay(
        input_path,
        output_path,
        window_ns=120_000_000_000,
        prediction_offset_ns=60_000_000_000,
        entry_price=0.4,
        sizing_config=sizing_config,
        fee_rate=0.0,
    )

    assert report.pnl_windows == 1
    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.DictReader(output_file))
    pnl_rows = [row for row in rows if row["pnl_usd"]]
    assert len(pnl_rows) == 1
    assert float(pnl_rows[0]["stake_usd"]) == pytest.approx(expected.stake_usd)
    assert float(pnl_rows[0]["kelly_fraction"]) == pytest.approx(expected.kelly_fraction)
    assert float(pnl_rows[0]["shares"]) == pytest.approx(expected.stake_usd / 0.4)


def test_replay_dynamic_sizing_rejects_stake_and_sizing_config_together(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    with input_path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        write_row(writer, "1", "100", "binance.book_ticker")

    sizing_config = SizingConfig(mode="dynamic", bankroll=10.0)
    with pytest.raises(ValueError):
        run_replay(
            input_path,
            output_path,
            window_ns=120_000_000_000,
            prediction_offset_ns=60_000_000_000,
            entry_price=0.4,
            stake_usd=10.0,
            sizing_config=sizing_config,
        )


def test_replay_applies_fees_to_pnl_of_an_accepted_bet(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    with input_path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        write_row(writer, "1", "100", "binance.book_ticker")
        write_row(writer, "59_000_000_000", "101", "binance.book_ticker")
        write_row(writer, "119_000_000_000", "102", "binance.book_ticker")
        write_row(writer, "120_000_000_000", "102", "binance.book_ticker")

    report = run_replay(
        input_path,
        output_path,
        window_ns=120_000_000_000,
        prediction_offset_ns=60_000_000_000,
        entry_price=0.4,
        stake_usd=10.0,
        fee_rate=0.1,
    )

    assert report.pnl_windows == 1
    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.DictReader(output_file))
    pnl_rows = [row for row in rows if row["pnl_usd"]]
    assert len(pnl_rows) == 1
    assert float(pnl_rows[0]["shares"]) == 25.0
    assert float(pnl_rows[0]["fees_usd"]) == 1.0
    assert float(pnl_rows[0]["pnl_usd"]) == 14.0
