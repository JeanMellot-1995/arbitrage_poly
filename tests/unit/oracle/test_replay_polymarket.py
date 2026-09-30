import csv

from arbitrage_poly.apps.replay import run_replay
from arbitrage_poly.polymarket.discovery import GammaMarketDiscovery
from arbitrage_poly.polymarket.historical_prices import ClobHistoricalPriceClient
from arbitrage_poly.polymarket.rest import RateLimitedRestClient

FIELDS = ["ts_ns", "exchange_ts_ns", "received_ts_ns", "price", "qty", "source", "seq"]
WINDOW_NS = 300_000_000_000


def _write_ticks(path) -> None:
    with path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        rows = [
            ("1", "100"),
            ("179_000_000_000", "101"),
            ("299_000_000_000", "102"),
            ("300_000_000_000", "102"),
        ]
        for ts_ns, price in rows:
            writer.writerow(
                {
                    "ts_ns": ts_ns,
                    "exchange_ts_ns": ts_ns,
                    "received_ts_ns": ts_ns,
                    "price": price,
                    "qty": "1",
                    "source": "binance.book_ticker",
                    "seq": "1",
                }
            )


def _market_payload():
    return [
        {
            "id": "0xabc",
            "slug": "btc-updown-5m-0",
            "clobTokenIds": '["up-token", "down-token"]',
            "outcomes": '["Up", "Down"]',
            "startDate": "1970-01-01T00:00:00.123456Z",
            "endDate": "1970-01-01T00:05:00Z",
        }
    ]


def _rest_client(fetch_json):
    return RateLimitedRestClient(fetch_json=fetch_json, min_request_interval_s=0.0, max_retries=0)


def test_replay_prices_a_window_with_polymarket_history(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    _write_ticks(input_path)

    discovery = GammaMarketDiscovery(client=_rest_client(lambda url, params: _market_payload()))

    def _prices(url, params):
        price = 0.60 if params["market"] == "up-token" else 0.35
        return {"history": [{"t": 175, "p": price}]}

    price_client = ClobHistoricalPriceClient(client=_rest_client(_prices))

    report = run_replay(
        input_path,
        output_path,
        window_ns=WINDOW_NS,
        stake_usd=10.0,
        polymarket_symbol="BTCUSDT",
        polymarket_discovery=discovery,
        polymarket_price_client=price_client,
    )

    assert report.pnl_enabled is True
    assert report.oracle_scored_windows == 1
    assert report.economic_priced_windows == 1
    assert report.pnl_windows == 1

    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.DictReader(output_file))
    priced_rows = [row for row in rows if row["economic_status"] == "priced"]
    assert len(priced_rows) == 1
    assert priced_rows[0]["market_id"] == "0xabc"
    assert priced_rows[0]["entry_price"] in ("0.6", "0.35")


def test_replay_rejects_a_price_in_the_uncertain_band(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    _write_ticks(input_path)

    discovery = GammaMarketDiscovery(client=_rest_client(lambda url, params: _market_payload()))

    def _prices(url, params):
        # UP is the Oracle's favored side (rising ticks) but its market price
        # (0.55) falls inside the default [0.45, 0.55] uncertain band, so no
        # bet should be placed even though the edge would otherwise qualify.
        price = 0.55 if params["market"] == "up-token" else 0.42
        return {"history": [{"t": 175, "p": price}]}

    price_client = ClobHistoricalPriceClient(client=_rest_client(_prices))

    report = run_replay(
        input_path,
        output_path,
        window_ns=WINDOW_NS,
        stake_usd=10.0,
        polymarket_symbol="BTCUSDT",
        polymarket_discovery=discovery,
        polymarket_price_client=price_client,
    )

    assert report.oracle_scored_windows == 1
    assert report.economic_priced_windows == 0
    assert report.pnl_windows == 0

    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.DictReader(output_file))
    statuses = {row["economic_status"] for row in rows if row["economic_status"]}
    assert statuses == {"uncertain_price_band"}


def _write_declining_ticks(path) -> None:
    with path.open("w", newline="", encoding="utf-8") as input_file:
        writer = csv.DictWriter(input_file, fieldnames=FIELDS)
        writer.writeheader()
        rows = [
            ("1", "100"),
            ("179_000_000_000", "99"),
            ("299_000_000_000", "98"),
            ("300_000_000_000", "98"),
        ]
        for ts_ns, price in rows:
            writer.writerow(
                {
                    "ts_ns": ts_ns,
                    "exchange_ts_ns": ts_ns,
                    "received_ts_ns": ts_ns,
                    "price": price,
                    "qty": "1",
                    "source": "binance.book_ticker",
                    "seq": "1",
                }
            )


def test_replay_only_buys_down_when_down_qualifies_and_up_does_not(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    _write_declining_ticks(input_path)

    discovery = GammaMarketDiscovery(client=_rest_client(lambda url, params: _market_payload()))

    def _prices(url, params):
        # UP is overpriced relative to the Oracle's 0.05 prob_up (disqualified);
        # DOWN is underpriced relative to its 0.95 prob_down (qualifies).
        price = 0.90 if params["market"] == "up-token" else 0.30
        return {"history": [{"t": 175, "p": price}]}

    price_client = ClobHistoricalPriceClient(client=_rest_client(_prices))

    report = run_replay(
        input_path,
        output_path,
        window_ns=WINDOW_NS,
        stake_usd=10.0,
        polymarket_symbol="BTCUSDT",
        polymarket_discovery=discovery,
        polymarket_price_client=price_client,
    )

    assert report.pnl_windows == 1
    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.DictReader(output_file))
    priced_rows = [row for row in rows if row["economic_status"] == "priced"]
    assert len(priced_rows) == 1
    assert priced_rows[0]["pnl_side"] == "DOWN"
    assert priced_rows[0]["entry_price"] == "0.3"


def test_replay_excludes_pnl_when_market_is_missing_but_keeps_oracle_score(tmp_path) -> None:
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "fair_values.csv"
    _write_ticks(input_path)

    discovery = GammaMarketDiscovery(client=_rest_client(lambda url, params: []))
    empty_history = _rest_client(lambda url, params: {"history": []})
    price_client = ClobHistoricalPriceClient(client=empty_history)

    report = run_replay(
        input_path,
        output_path,
        window_ns=WINDOW_NS,
        stake_usd=10.0,
        polymarket_symbol="BTCUSDT",
        polymarket_discovery=discovery,
        polymarket_price_client=price_client,
    )

    assert report.oracle_scored_windows == 1
    assert report.economic_priced_windows == 0
    assert report.pnl_windows == 0
    assert report.total_pnl_usd == 0.0

    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.DictReader(output_file))
    statuses = {row["economic_status"] for row in rows if row["economic_status"]}
    assert statuses == {"missing_market"}
