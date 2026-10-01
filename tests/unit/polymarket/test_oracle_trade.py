import pytest

from arbitrage_poly.apps.oracle_trade import LIMIT, MARKET, execute_decision, plan_order

DECISION = {
    "window_start_utc": "2026-09-30T12:55:00.000000Z",
    "window_end_utc": "2026-09-30T13:00:00.000000Z",
    "decision_ts_utc": "2026-09-30T12:58:00.100000Z",
    "prob_up": 0.2,
    "side": "DOWN",
}


def test_fair_value_below_ask_places_limit_at_fair_value() -> None:
    plan = plan_order(0.678, 0.72, stake_usd=10.0)
    assert plan is not None
    assert plan.kind == LIMIT
    assert plan.price == 0.67
    assert plan.shares == pytest.approx(14.93)


def test_fair_value_at_or_above_ask_executes_market() -> None:
    plan = plan_order(0.80, 0.72, stake_usd=10.0)
    assert plan is not None
    assert plan.kind == MARKET
    assert plan.price == 0.73

    equal = plan_order(0.72, 0.72, stake_usd=10.0)
    assert equal is not None and equal.kind == MARKET


def test_market_price_is_capped_and_minimum_shares_applied() -> None:
    plan = plan_order(0.995, 0.99, stake_usd=1.0)
    assert plan is not None
    assert plan.kind == MARKET
    assert plan.price == 0.99
    assert plan.shares == 5.0


def test_no_ask_falls_back_to_limit_and_tiny_fair_value_is_skipped() -> None:
    plan = plan_order(0.55, None, stake_usd=10.0)
    assert plan is not None and plan.kind == LIMIT and plan.price == 0.55
    assert plan_order(0.004, 0.5, stake_usd=10.0) is None


def test_execute_decision_buys_down_token_with_limit_when_share_too_expensive() -> None:
    sent = []

    def send_order(**kwargs):
        sent.append(kwargs)
        return {"success": True}

    row = execute_decision(
        DECISION,
        find_tokens=lambda window_start_ns: ("up-token", "down-token"),
        fetch_best_ask=lambda token_id: 0.85,
        stake_usd=10.0,
        send_order=send_order,
    )

    assert row["fair_value"] == pytest.approx(0.8)
    assert row["order_kind"] == LIMIT
    assert row["status"] == "sent"
    assert sent == [
        {
            "token_id": "down-token",
            "price": 0.8,
            "quantity": 12.5,
            "side": "BUY",
            "order_type": "GTC",
        }
    ]


def test_execute_decision_dry_run_and_missing_market() -> None:
    dry = execute_decision(
        {**DECISION, "prob_up": 0.9, "side": "UP"},
        find_tokens=lambda window_start_ns: ("up-token", "down-token"),
        fetch_best_ask=lambda token_id: 0.6,
        stake_usd=10.0,
        send_order=None,
    )
    assert dry["token_id"] == "up-token"
    assert dry["order_kind"] == MARKET
    assert dry["order_price"] == 0.61
    assert dry["status"] == "dry_run"

    missing = execute_decision(
        DECISION,
        find_tokens=lambda window_start_ns: None,
        fetch_best_ask=lambda token_id: 0.6,
        stake_usd=10.0,
        send_order=None,
    )
    assert missing["status"] == "skipped"
    assert missing["detail"] == "no_open_market"


def test_execute_decision_records_order_errors() -> None:
    def send_order(**kwargs):
        raise RuntimeError("rejected")

    row = execute_decision(
        DECISION,
        find_tokens=lambda window_start_ns: ("up-token", "down-token"),
        fetch_best_ask=lambda token_id: 0.7,
        stake_usd=10.0,
        send_order=send_order,
    )
    assert row["status"] == "error"
    assert row["detail"] == "rejected"
