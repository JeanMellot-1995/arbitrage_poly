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
class _TokenProvider(_Provider):
    market_by_window = {0: ("up-token", "down-token")}


class _RecordingExecutor:
    def __init__(self) -> None:
        self.orders = []

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"success": True}


def _tick(ts_ns: int, price: float, seq: int) -> Tick:
    return Tick(ts_ns=ts_ns, price=price, qty=1.0, source="binance.book_ticker", seq=seq)


def test_live_loop_offset_decides_once_per_window_and_executes_buy() -> None:
    second = 1_000_000_000

    async def scenario() -> None:
        queue = asyncio.Queue()

        async def transport_factory():
            return _IdleTransport()

        reader = BinancePriceReader(queue=queue, transport_factory=transport_factory)
        decisions = []
        executor = _RecordingExecutor()

        async def record(decision) -> None:
            decisions.append(decision)
            if len(decisions) == 2:
                reader.stop()

        # Before T-120s (ignored), then two ticks inside the offset of window 0,
        # then one tick inside the offset of the next window.
        for seq, (ts_ns, price) in enumerate(
            [
                (10 * second, 100.0),
                (170 * second, 100.5),
                (190 * second, 101.0),
                (200 * second, 101.5),
                (490 * second, 101.0),
            ],
            start=1,
        ):
            await queue.put(_tick(ts_ns, price, seq))

        await run_live(
            reader=reader,
            oracle=PriceOracle(volatility_sampling_interval_ns=10_000),
            snapshot_provider=_TokenProvider(),
            pricing_config=PricingConfig(min_net_edge=0.01),
            sizing_config=SizingConfig(mode="fixed", fixed_stake_usd=10.0),
            quantity=1.0,
            on_decision=record,
            executor=executor,
            offset_ns=120 * second,
        )

        assert [d.tick.ts_ns for d in decisions] == [190 * second, 490 * second]
        assert all(d.fair_value.remaining_ns <= 120 * second for d in decisions)
        accepted_window0 = decisions[0].opportunity.accepted
        assert accepted_window0 is True
        window0_orders = [o for o in executor.orders if o["token_id"] in {"up-token", "down-token"}]
        assert len(window0_orders) == (1 if accepted_window0 else 0)
        for order in window0_orders:
            assert order["side"] == "BUY"
            assert order["order_type"] == "GTC"
            assert order["quantity"] >= 5.0
            expected = "up-token" if decisions[0].opportunity.side == "UP" else "down-token"
            assert order["token_id"] == expected

    asyncio.run(scenario())
