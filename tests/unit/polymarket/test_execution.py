from arbitrage_poly.apps import execute_order, market_order
from arbitrage_poly.polymarket.execution import _floor_to_tick, load_api_credentials
from arbitrage_poly.polymarket.models import MarketLookupResult


def test_load_api_credentials_reads_json_and_key_value_formats(tmp_path) -> None:
    json_file = tmp_path / "creds.json"
    json_file.write_text('{"API Key": "k", "Address": "0xabc"}', encoding="utf-8")
    assert load_api_credentials(json_file) == {"API Key": "k", "Address": "0xabc"}

    text_file = tmp_path / "api"
    text_file.write_text("API Key:\nkey-1\nAPI Secret:\nsecret-1\n", encoding="utf-8")
    assert load_api_credentials(text_file) == {"API Key": "key-1", "API Secret": "secret-1"}

    assert load_api_credentials(tmp_path / "missing.json") == {}


def test_floor_to_tick_never_rounds_up() -> None:
    assert _floor_to_tick(0.5600044800358403) == 0.56
    assert _floor_to_tick(0.569999) == 0.56
    assert _floor_to_tick(0.57) == 0.57


def test_execute_order_dry_run_does_not_create_executor(monkeypatch, capsys) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("executor must not be created in dry run")

    monkeypatch.setattr(execute_order, "PolymarketExecutor", fail)
    execute_order.main(["--token-id", "tok", "--price", "0.55", "--quantity", "5", "--side", "BUY"])
    assert "Dry run only" in capsys.readouterr().out


def test_market_order_dry_run_prices_above_best_ask(monkeypatch, capsys) -> None:
    class _Market:
        up_token_id = "up-token"
        down_token_id = "down-token"
        question = "BTC up or down?"

    class _Discovery:
        def __init__(self, **kwargs) -> None:
            pass

        def find_market_for_window(self, **kwargs):
            return MarketLookupResult(status="found", market=_Market())

    def fail(*args, **kwargs):
        raise AssertionError("executor must not be created in dry run")

    monkeypatch.setattr(market_order, "GammaMarketDiscovery", _Discovery)
    monkeypatch.setattr(market_order, "_best_ask", lambda client, token_id: 0.62)
    monkeypatch.setattr(market_order, "PolymarketExecutor", fail)

    assert market_order.main(["--side", "DOWN", "--quantity", "10"]) == 0
    out = capsys.readouterr().out
    assert "token_id=down-token" in out
    assert "price=0.63" in out
    assert "shares=15.87" in out
    assert "Dry run only" in out
