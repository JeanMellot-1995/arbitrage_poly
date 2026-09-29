"""Live, read-only Binance -> Oracle -> Polymarket paper-trading loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from arbitrage_poly.models import FairValue, Tick
from arbitrage_poly.oracle.oracle import PriceOracle
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


class SnapshotProvider(Protocol):
    def snapshot(self, fair_value: FairValue) -> OrderBookSnapshot | None: ...


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
) -> None:
    """Run the read-only decision loop until cancelled or ``reader.stop()``.

    The provider is intentionally injected so this loop can use polling in v1,
    a CLOB WebSocket later, or a deterministic fake in unit tests.
    """

    reader_task = asyncio.create_task(reader.run())
    try:
        while not reader.stopped:
            tick = await reader.queue.get()
            fair_value = oracle.observe(tick)
            if fair_value is None:
                continue
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
            sizing = compute_sizing(
                accepted=opportunity.accepted,
                config=sizing_config,
                probability=opportunity.fair_probability,
                price=opportunity.executable_price,
            ) if opportunity.accepted else compute_sizing(
                accepted=False,
                config=sizing_config,
            )
            decision = LiveDecision(
                fair_value=fair_value,
                opportunity=opportunity,
                stake_usd=sizing.stake_usd,
                tick=tick,
            )
            if on_decision is not None:
                result = on_decision(decision)
                if result is not None:
                    await result
    finally:
        reader.stop()
        reader_task.cancel()
        await asyncio.gather(reader_task, return_exceptions=True)