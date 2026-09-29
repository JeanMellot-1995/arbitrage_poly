import pytest

from arbitrage_poly.models import FairValue
from arbitrage_poly.pricing import (
    OrderBookLevel,
    OrderBookSnapshot,
    PricingConfig,
    SizingConfig,
    compute_sizing,
    evaluate_opportunity,
)


def _fair(prob_up: float = 0.7, ts_ns: int = 1_000) -> FairValue:
    return FairValue(
        ts_ns=ts_ns,
        window_start_ns=0,
        reference_price=100.0,
        current_price=101.0,
        prob_up=prob_up,
        prob_down=1.0 - prob_up,
        model="test",
        volatility=0.1,
        remaining_ns=100,
    )


def _book(ts_ns: int = 1_000, ask: float = 0.60, bid: float = 0.58) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        ts_ns=ts_ns,
        up_asks=(OrderBookLevel(ask, 10.0),),
        up_bids=(OrderBookLevel(bid, 10.0),),
        down_asks=(OrderBookLevel(0.40, 10.0),),
        down_bids=(OrderBookLevel(0.38, 10.0),),
    )


def test_accepts_up_and_passes_acceptance_to_sizing() -> None:
    opportunity = evaluate_opportunity(
        _fair(), _book(), quantity=2.0, config=PricingConfig(min_net_edge=0.01)
    )

    assert opportunity.accepted is True
    assert opportunity.side == "UP"
    assert opportunity.edge is not None
    assert opportunity.net_edge == pytest.approx(opportunity.edge.net_edge)

    sizing = compute_sizing(
        accepted=opportunity.accepted,
        config=SizingConfig(mode="fixed", fixed_stake_usd=10.0),
    )
    assert sizing.stake_usd == 10.0


def test_directional_gate_does_not_buy_the_minority_side() -> None:
    opportunity = evaluate_opportunity(
        _fair(prob_up=0.95),
        _book(ask=0.99),
        quantity=1.0,
        config=PricingConfig(min_net_edge=0.0),
    )

    assert opportunity.accepted is False
    assert opportunity.reason == "edge_below_threshold"


def test_rejects_insufficient_depth() -> None:
    opportunity = evaluate_opportunity(
        _fair(), _book(), quantity=11.0, config=PricingConfig(min_net_edge=0.0)
    )

    assert opportunity.accepted is False
    assert opportunity.reason == "insufficient_depth"


def test_weighted_depth_and_slippage_are_included_in_net_edge() -> None:
    book = OrderBookSnapshot(
        ts_ns=1_000,
        up_asks=(OrderBookLevel(0.60, 1.0), OrderBookLevel(0.62, 2.0)),
        up_bids=(OrderBookLevel(0.58, 10.0),),
    )
    opportunity = evaluate_opportunity(
        _fair(), book, quantity=3.0, config=PricingConfig(min_net_edge=0.01)
    )

    assert opportunity.accepted is True
    assert opportunity.executable_price == pytest.approx((0.60 + 2 * 0.62) / 3)
    assert opportunity.edge is not None
    assert opportunity.edge.slippage == pytest.approx((0.62 * 2 / 3) + 0.60 / 3 - 0.60)


def test_limit_price_is_floored_and_passive_crossing_uses_taker_fee() -> None:
    opportunity = evaluate_opportunity(
        _fair(),
        _book(ask=0.60, bid=0.60),
        quantity=1.0,
        config=PricingConfig(execution_mode="passive", min_net_edge=0.01, tick_size=0.01),
    )

    assert opportunity.accepted is True
    assert opportunity.edge is not None
    assert opportunity.edge.fee_rate == pytest.approx(0.0007)
    assert opportunity.edge.limit_price <= opportunity.edge.max_limit_price
    assert opportunity.edge.limit_price == pytest.approx(0.68)


def test_stale_snapshot_is_rejected_before_pricing() -> None:
    opportunity = evaluate_opportunity(
        _fair(ts_ns=1_000),
        _book(ts_ns=1_000),
        quantity=1.0,
        config=PricingConfig(max_snapshot_age_ns=10),
        now_ns=1_011,
    )

    assert opportunity.accepted is False
    assert opportunity.reason == "stale_order_book"