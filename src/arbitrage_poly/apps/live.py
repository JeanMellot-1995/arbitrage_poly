"""Live Binance -> Oracle -> Polymarket loop, with optional real order execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from arbitrage_poly.models import FairValue, Tick
from arbitrage_poly.oracle.oracle import PriceOracle
from arbitrage_poly.polymarket.execution import PolymarketExecutor
from arbitrage_poly.price_collection.binance_ws import BinancePriceReader
from arbitrage_poly.pricing import (
    Opportunity,
    OrderBookSnapshot,
    PricingConfig,
    SizingConfig,
    compute_sizing,
    evaluate_opportunity,
)

LOGGER = logging.getLogger(__name__)
MIN_ORDER_SHARES = 5.0


class SnapshotProvider(Protocol):
    def snapshot(self, fair_value: FairValue) -> OrderBookSnapshot | None: ...
    @property
    def market_by_window(self) -> dict[int, tuple[str, str]]: ...


@dataclass(frozen=True, slots=True)
class LiveDecision:
    fair_value: FairValue
    opportunity: Opportunity
    stake_usd: float
    tick: Tick


DecisionHandler = Callable[[LiveDecision], Awaitable[None] | None]


async def run_live(
    *,
    reader: BinancePriceReader,
    oracle: PriceOracle,
    snapshot_provider: SnapshotProvider,
    pricing_config: PricingConfig,
    sizing_config: SizingConfig,
    quantity: float,
    on_decision: DecisionHandler | None = None,
    executor: PolymarketExecutor | None = None,
    offset_ns: int = 0,
) -> None:
    """Run the decision loop until cancelled or ``reader.stop()``.

    When ``executor`` is provided, accepted opportunities with a positive
    stake trigger a real ``BUY`` order on the Polymarket CLOB.

    When ``offset_ns`` is positive, ticks are ignored until ``offset_ns``
    before the window closes, and at most one decision is made per window --
    the same ``prediction_offset_s`` convention used by
    ``arbitrage_poly.apps.live_decisions``. When ``offset_ns`` is 0 (default),
    every tick is evaluated as soon as it arrives.

    The provider is intentionally injected so this loop can use polling in v1,
    a CLOB WebSocket later, or a deterministic fake in unit tests.
    """

    decided_window_start_ns: int | None = None
    reader_task = asyncio.create_task(reader.run())
    try:
        while not reader.stopped:
            tick = await reader.queue.get()
            fair_value = oracle.observe(tick)
            if fair_value is None:
                continue
            if offset_ns > 0:
                if fair_value.remaining_ns > offset_ns:
                    continue
                if fair_value.window_start_ns == decided_window_start_ns:
                    continue
                decided_window_start_ns = fair_value.window_start_ns
            try:
                snapshot = await asyncio.to_thread(snapshot_provider.snapshot, fair_value)
            except Exception as exc:
                LOGGER.warning("live_snapshot_failed", extra={"error": str(exc)})
                snapshot = None
            if snapshot is None:
                opportunity = Opportunity(
                    accepted=False,
                    side=None,
                    reason="book_unavailable",
                    fair_value_ts_ns=fair_value.ts_ns,
                )
            else:
                opportunity = evaluate_opportunity(
                    fair_value,
                    snapshot,
                    quantity=quantity,
                    config=pricing_config,
                    now_ns=fair_value.ts_ns,
                )
            sizing = (
                compute_sizing(
                    accepted=opportunity.accepted,
                    config=sizing_config,
                    probability=opportunity.fair_probability,
                    price=opportunity.executable_price,
                )
                if opportunity.accepted
                else compute_sizing(
                    accepted=False,
                    config=sizing_config,
                )
            )
            decision = LiveDecision(
                fair_value=fair_value,
                opportunity=opportunity,
                stake_usd=sizing.stake_usd,
                tick=tick,
            )

            if executor is not None:
                if opportunity.accepted and sizing.stake_usd > 0:
                    tokens = snapshot_provider.market_by_window.get(fair_value.window_start_ns)
                    if tokens:
                        try:
                            token_id = tokens[0] if opportunity.side == "UP" else tokens[1]
                            price = opportunity.executable_price or 0.5
                            qty_shares = max(sizing.stake_usd / price, MIN_ORDER_SHARES)

                            LOGGER.info(
                                "triggering_live_order window_start_ns=%s side=%s token_id=%s "
                                "price=%s shares=%s",
                                fair_value.window_start_ns,
                                opportunity.side,
                                token_id,
                                price,
                                qty_shares,
                            )

                            # Always BUY: the UP/DOWN token itself encodes the directional bet.
                            order_response = await asyncio.to_thread(
                                executor.place_order,
                                token_id=token_id,
                                price=round(price, 2),
                                quantity=round(qty_shares, 2),
                                side="BUY",
                                order_type="GTC",
                            )
                            LOGGER.info(
                                "live_order_confirmed window_start_ns=%s side=%s token_id=%s "
                                "price=%s shares=%s response=%s",
                                fair_value.window_start_ns,
                                opportunity.side,
                                token_id,
                                price,
                                qty_shares,
                                order_response,
                            )
                        except Exception as exc:
                            LOGGER.error(
                                "live_order_execution_failed window_start_ns=%s error=%s",
                                fair_value.window_start_ns,
                                exc,
                                exc_info=True,
                            )
                    else:
                        LOGGER.warning(
                            "live_order_skipped_no_market window_start_ns=%s side=%s",
                            fair_value.window_start_ns,
                            opportunity.side,
                        )
                else:
                    LOGGER.info(
                        "live_order_skipped window_start_ns=%s reason=%s accepted=%s stake_usd=%s",
                        fair_value.window_start_ns,
                        opportunity.reason,
                        opportunity.accepted,
                        sizing.stake_usd,
                    )

            if on_decision is not None:
                result = on_decision(decision)
                if result is not None:
                    await result
    finally:
        reader.stop()
        reader_task.cancel()
        await asyncio.gather(reader_task, return_exceptions=True)
