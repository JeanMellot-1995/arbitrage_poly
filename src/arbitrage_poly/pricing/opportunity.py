"""Directional opportunity decisions built from Oracle and book contracts."""

from __future__ import annotations

from dataclasses import dataclass

from arbitrage_poly.models import FairValue
from arbitrage_poly.pricing.edge import (
    EdgeEstimate,
    OrderBookSnapshot,
    Outcome,
    PricingConfig,
    calculate_edge,
)

REJECTION_REASONS = frozenset(
    {
        "stale_fair_value",
        "stale_order_book",
        "missing_side",
        "book_unavailable",
        "insufficient_depth",
        "invalid_price",
        "invalid_probability",
        "edge_below_threshold",
        "configuration_error",
    }
)


@dataclass(frozen=True, slots=True)
class Opportunity:
    accepted: bool
    side: Outcome | None
    reason: str | None
    fair_probability: float | None = None
    executable_price: float | None = None
    net_edge: float | None = None
    edge: EdgeEstimate | None = None
    fair_value_ts_ns: int | None = None
    order_book_ts_ns: int | None = None

    def __post_init__(self) -> None:
        if self.accepted and (self.side is None or self.reason is not None or self.edge is None):
            raise ValueError("accepted opportunity requires a side, edge and no rejection reason")
        if not self.accepted and self.reason not in REJECTION_REASONS:
            raise ValueError(f"unknown opportunity rejection reason: {self.reason}")


def _rejected(reason: str, fair_value: FairValue, snapshot: OrderBookSnapshot) -> Opportunity:
    return Opportunity(
        accepted=False,
        side=None,
        reason=reason,
        fair_value_ts_ns=fair_value.ts_ns,
        order_book_ts_ns=snapshot.ts_ns,
    )


def evaluate_opportunity(
    fair_value: FairValue,
    snapshot: OrderBookSnapshot,
    *,
    quantity: float,
    config: PricingConfig,
    now_ns: int | None = None,
) -> Opportunity:
    """Apply directional, freshness, depth and net-edge gates in order."""

    if quantity <= 0.0:
        return _rejected("configuration_error", fair_value, snapshot)
    if not 0.0 <= fair_value.prob_up <= 1.0 or abs(
        fair_value.prob_up + fair_value.prob_down - 1.0
    ) > 1e-12:
        return _rejected("invalid_probability", fair_value, snapshot)
    if fair_value.remaining_ns < 0:
        return _rejected("invalid_probability", fair_value, snapshot)
    if now_ns is not None:
        if (
            config.max_snapshot_age_ns is not None
            and now_ns - snapshot.ts_ns > config.max_snapshot_age_ns
        ):
            return _rejected("stale_order_book", fair_value, snapshot)
        if (
            config.max_fair_value_age_ns is not None
            and now_ns - fair_value.ts_ns > config.max_fair_value_age_ns
        ):
            return _rejected("stale_fair_value", fair_value, snapshot)

    side: Outcome | None = None
    if (
        fair_value.prob_up > 0.5
        and snapshot.up_asks
        and snapshot.up_asks[0].price < fair_value.prob_up
    ):
        side = "UP"
    elif (
        fair_value.prob_down > 0.5
        and snapshot.down_asks
        and snapshot.down_asks[0].price < fair_value.prob_down
    ):
        side = "DOWN"
    if side is None:
        if not snapshot.up_asks and not snapshot.down_asks:
            return _rejected("missing_side", fair_value, snapshot)
        return _rejected("edge_below_threshold", fair_value, snapshot)

    try:
        edge = calculate_edge(fair_value, snapshot, side=side, quantity=quantity, config=config)
    except ValueError as exc:
        reason = str(exc)
        if reason not in REJECTION_REASONS:
            reason = "configuration_error"
        return _rejected(reason, fair_value, snapshot)

    if edge.limit_price <= 0.0 or edge.limit_price > 1.0:
        return _rejected("invalid_price", fair_value, snapshot)

    # A passive quote that crosses the best ask is a taker execution. Reprice
    # the economic limit with the taker fee before making the final decision.
    if (
        config.execution_mode == "passive"
        and edge.quoted_price is not None
        and edge.quoted_price >= edge.best_ask
    ):
        try:
            edge = calculate_edge(
                fair_value,
                snapshot,
                side=side,
                quantity=quantity,
                config=config,
                execution_mode="passive",
                fee_rate=config.taker_fee_rate,
            )
        except ValueError as exc:
            reason = str(exc) if str(exc) in REJECTION_REASONS else "configuration_error"
            return _rejected(reason, fair_value, snapshot)

    if edge.net_edge < config.min_net_edge:
        return _rejected("edge_below_threshold", fair_value, snapshot)
    return Opportunity(
        accepted=True,
        side=side,
        reason=None,
        fair_probability=edge.fair_probability,
        executable_price=edge.average_ask,
        net_edge=edge.net_edge,
        edge=edge,
        fair_value_ts_ns=fair_value.ts_ns,
        order_book_ts_ns=snapshot.ts_ns,
    )