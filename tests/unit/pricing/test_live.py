import asyncio

from arbitrage_poly.apps.live import run_live
from arbitrage_poly.models import Tick
from arbitrage_poly.oracle.oracle import PriceOracle
from arbitrage_poly.price_collection.binance_ws import BinancePriceReader
from arbitrage_poly.pricing import (
    OrderBookLevel,
    OrderBookSnapshot,
    PricingConfig,
    SizingConfig,
)


class _Provider:
    def snapshot(self, fair_value):
        return OrderBookSnapshot(
            ts_ns=fair_value.ts_ns,
            up_asks=(OrderBookLevel(0.60, 10.0),),
            up_bids=(OrderBookLevel(0.58, 10.0),),
            down_asks=(OrderBookLevel(0.40, 10.0),),
            down_bids=(OrderBookLevel(0.38, 10.0),),
        )


class _IdleTransport:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def aclose(self):
        return None


class _UnavailableProvider:
    def snapshot(self, fair_value):
        return None


def test_live_loop_passes_opportunity_to_sizing() -> None:
    async def scenario() -> None:
        queue = asyncio.Queue()
        async def transport_factory():
            return _IdleTransport()

        reader = BinancePriceReader(queue=queue, transport_factory=transport_factory)
        decisions = []

        async def stop_after_decision(decision) -> None:
            if decision.opportunity.accepted:
                decisions.append(decision)
                reader.stop()

        await queue.put(
            Tick(
                ts_ns=1_000,
                price=100.0,
                qty=1.0,
                source="binance.book_ticker",
                seq=1,
            )
        )
        await queue.put(
            Tick(
                ts_ns=2_000,
                price=101.0,
                qty=1.0,
                source="binance.book_ticker",
                seq=2,
            )
        )
        await run_live(
            reader=reader,
            oracle=PriceOracle(volatility_sampling_interval_ns=10_000),
            snapshot_provider=_Provider(),
            pricing_config=PricingConfig(min_net_edge=0.01),
            sizing_config=SizingConfig(mode="fixed", fixed_stake_usd=10.0),
            quantity=1.0,
            on_decision=stop_after_decision,
        )

        assert len(decisions) == 1
        assert decisions[0].opportunity.accepted is True
        assert decisions[0].stake_usd == 10.0

    asyncio.run(scenario())


def test_live_loop_emits_book_unavailable_decision() -> None:
    async def scenario() -> None:
        queue = asyncio.Queue()
        reader = BinancePriceReader(queue=queue, transport_factory=lambda: _idle_transport())
        decisions = []

        async def stop_after_decision(decision) -> None:
            decisions.append(decision)
            reader.stop()

        await queue.put(
            Tick(
                ts_ns=1_000,
                price=100.0,
                qty=1.0,
                source="binance.book_ticker",
                seq=1,
            )
        )
        await run_live(
            reader=reader,
            oracle=PriceOracle(),
            snapshot_provider=_UnavailableProvider(),
            pricing_config=PricingConfig(),
            sizing_config=SizingConfig(mode="fixed", fixed_stake_usd=10.0),
            quantity=1.0,
            on_decision=stop_after_decision,
        )

        assert len(decisions) == 1
        assert decisions[0].opportunity.reason == "book_unavailable"
        assert decisions[0].stake_usd == 0.0

    async def _idle_transport():
        return _IdleTransport()

    asyncio.run(scenario())