import asyncio

from arbitrage_poly.apps.live_decisions import (
    AGG_TRADE_SOURCE,
    WINDOW_NS,
    run_live_decisions,
)
from arbitrage_poly.models import Tick
from arbitrage_poly.oracle.oracle import PriceOracle
from arbitrage_poly.price_collection.binance_ws import BinancePriceReader

OFFSET_NS = 60_000_000_000
WINDOW_START_NS = 300_000_000_000


class _IdleTransport:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _make_reader() -> tuple[BinancePriceReader, asyncio.Queue]:
    queue: asyncio.Queue = asyncio.Queue()

    async def transport_factory():
        return _IdleTransport()

    return BinancePriceReader(queue=queue, transport_factory=transport_factory), queue


def test_records_one_decision_per_window_at_offset() -> None:
    async def scenario() -> None:
        reader, queue = _make_reader()
        decisions: list[dict] = []

        def on_decision(row: dict) -> None:
            decisions.append(row)
            if len(decisions) >= 1:
                reader.stop()

        # Before the offset: sets the reference price, no decision yet.
        await queue.put(
            Tick(ts_ns=WINDOW_START_NS, price=100.0, qty=1.0, source=AGG_TRADE_SOURCE, seq=1)
        )
        # Inside the t-60s window: this is the frozen decision.
        await queue.put(
            Tick(
                ts_ns=WINDOW_START_NS + 250_000_000_000,
                price=101.0,
                qty=1.0,
                source=AGG_TRADE_SOURCE,
                seq=2,
            )
        )

        await run_live_decisions(
            reader=reader,
            oracle=PriceOracle(
                window_ns=WINDOW_NS,
                source=AGG_TRADE_SOURCE,
                volatility_sampling_interval_ns=1_000_000_000,
            ),
            offset_ns=OFFSET_NS,
            on_decision=on_decision,
        )

        assert len(decisions) == 1
        row = decisions[0]
        assert row["side"] == "UP"
        assert row["reference_price"] == 100.0
        assert row["current_price"] == 101.0
        assert row["prediction_cutoff_utc"] == "1970-01-01T00:09:00.000000Z"

    asyncio.run(scenario())


def test_second_tick_in_same_window_does_not_duplicate_decision() -> None:
    async def scenario() -> None:
        reader, queue = _make_reader()
        decisions: list[dict] = []

        def on_decision(row: dict) -> None:
            decisions.append(row)
            if len(decisions) >= 2:
                reader.stop()

        await queue.put(
            Tick(ts_ns=WINDOW_START_NS, price=100.0, qty=1.0, source=AGG_TRADE_SOURCE, seq=1)
        )
        await queue.put(
            Tick(
                ts_ns=WINDOW_START_NS + 250_000_000_000,
                price=101.0,
                qty=1.0,
                source=AGG_TRADE_SOURCE,
                seq=2,
            )
        )
        # Second tick still inside the same window, past the offset: no new decision.
        await queue.put(
            Tick(
                ts_ns=WINDOW_START_NS + 260_000_000_000,
                price=102.0,
                qty=1.0,
                source=AGG_TRADE_SOURCE,
                seq=3,
            )
        )
        # Next window's tick past its own offset: this is the second, distinct decision.
        await queue.put(
            Tick(
                ts_ns=WINDOW_START_NS + WINDOW_NS + 250_000_000_000,
                price=103.0,
                qty=1.0,
                source=AGG_TRADE_SOURCE,
                seq=4,
            )
        )

        await run_live_decisions(
            reader=reader,
            oracle=PriceOracle(
                window_ns=WINDOW_NS,
                source=AGG_TRADE_SOURCE,
                volatility_sampling_interval_ns=1_000_000_000,
            ),
            offset_ns=OFFSET_NS,
            on_decision=on_decision,
        )

        assert len(decisions) == 2
        assert decisions[0]["current_price"] == 101.0
        assert decisions[1]["current_price"] == 103.0

    asyncio.run(scenario())


def test_resume_skips_window_already_decided() -> None:
    async def scenario() -> None:
        reader, queue = _make_reader()
        decisions: list[dict] = []

        def on_decision(row: dict) -> None:
            decisions.append(row)
            reader.stop()

        await queue.put(
            Tick(ts_ns=WINDOW_START_NS, price=100.0, qty=1.0, source=AGG_TRADE_SOURCE, seq=1)
        )
        await queue.put(
            Tick(
                ts_ns=WINDOW_START_NS + 250_000_000_000,
                price=101.0,
                qty=1.0,
                source=AGG_TRADE_SOURCE,
                seq=2,
            )
        )
        # A tick from the next window should still produce a fresh decision.
        await queue.put(
            Tick(
                ts_ns=WINDOW_START_NS + WINDOW_NS + 250_000_000_000,
                price=103.0,
                qty=1.0,
                source=AGG_TRADE_SOURCE,
                seq=3,
            )
        )

        await run_live_decisions(
            reader=reader,
            oracle=PriceOracle(
                window_ns=WINDOW_NS,
                source=AGG_TRADE_SOURCE,
                volatility_sampling_interval_ns=1_000_000_000,
            ),
            offset_ns=OFFSET_NS,
            on_decision=on_decision,
            last_decided_window_start_ns=WINDOW_START_NS,
        )

        assert len(decisions) == 1
        assert decisions[0]["current_price"] == 103.0

    asyncio.run(scenario())
