from arbitrage_poly.apps.binance_windows import evaluate_windows, fetch_klines_1s

WINDOW_MS = 300_000


def test_evaluate_windows_scores_each_window_without_look_ahead() -> None:
    start_ms = 1_800_000_000_000 // WINDOW_MS * WINDOW_MS
    cutoff_ms = start_ms + WINDOW_MS - 60_000
    klines = [
        (start_ms - 1000, 1.0, 1.0),
        (start_ms, 100.0, 100.0),
        (start_ms + 1000, 100.0, 101.0),
        (cutoff_ms - 1000, 101.0, 102.0),
        (cutoff_ms, 50.0, 50.0),
        (start_ms + WINDOW_MS - 1000, 50.0, 99.0),
    ]

    rows = list(evaluate_windows(klines, start_ms, start_ms + 2 * WINDOW_MS, 60_000, "tick_ewma"))

    first, second = rows
    assert first["open_price"] == 100.0
    assert first["close_price"] == 99.0
    assert first["outcome_binance"] == "DOWN"
    assert first["reference_price"] == 100.0
    assert first["current_price"] == 102.0
    assert first["side"] == "UP"
    assert first["correct"] is False
    assert second["exclusion_reason"] == "no_binance_klines"


def test_evaluate_windows_horizon_model_uses_warmup_history() -> None:
    start_ms = 1_800_000_000_000 // WINDOW_MS * WINDOW_MS
    warmup_start_ms = start_ms - 3_600_000
    klines = [
        (ts, price, price)
        for ts in range(warmup_start_ms, start_ms + WINDOW_MS, 1000)
        for price in [100.0 if (ts // 60_000) % 2 else 100.1]
    ]

    (row,) = evaluate_windows(klines, start_ms, start_ms + WINDOW_MS, 60_000)

    assert row["exclusion_reason"] in ("", "binance_tie")
    assert row["reference_price"] == klines[3600][1]
    assert 0.1 < row["volatility"] < 5.0

    (cold,) = evaluate_windows(klines[3600:], start_ms, start_ms + WINDOW_MS, 60_000)
    assert cold["exclusion_reason"] == "volatility_warmup"


def test_fetch_klines_pages_until_end() -> None:
    urls: list[str] = []

    def fake_get_json(url: str) -> list:
        urls.append(url)
        start = int(url.split("startTime=")[1].split("&")[0])
        return [[ts, "1", "0", "0", "2"] for ts in range(start, min(start + 3000, 5000), 1000)]

    klines = list(fetch_klines_1s("BTCUSDT", 0, 5000, get_json=fake_get_json))

    assert [kline[0] for kline in klines] == [0, 1000, 2000, 3000, 4000]
    assert klines[0][1:] == (1.0, 2.0)
    assert len(urls) == 2
