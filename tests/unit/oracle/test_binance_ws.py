import asyncio
import json

import pytest

from arbitrage_poly.clock import MonotonicClock
from arbitrage_poly.models import Tick
from arbitrage_poly.price_collection.binance_ws import (
    BinanceMessageError,
    BinancePriceReader,
    QueueOverflowError,
    parse_message,
)


class FixedClock(MonotonicClock):
    def now_ns(self) -> int:
        return 2_000_000_000_000_000_000


def agg_trade(sequence: int = 10) -> str:
    return json.dumps(
        {
            "stream": "btcusdt@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": 1_700_000_000_000,
                "s": "BTCUSDT",
                "p": "65000.10",
                "q": "0.004",
                "a": sequence,
            },
        }
    )


def book_ticker(sequence: int = 20) -> str:
    return json.dumps(
        {
            "stream": "btcusdt@bookTicker",
            "data": {
                "e": "bookTicker",
                "E": 1_700_000_000_001,
                "s": "BTCUSDT",
                "b": "65000.00",
                "B": "0.5",
                "a": "65000.20",
                "A": "0.4",
                "u": sequence,
            },
        }
    )


def test_parse_agg_trade() -> None:
    tick = parse_message(agg_trade(), FixedClock(0, 0))

    assert tick == Tick(
        ts_ns=2_000_000_000_000_000_000,
        price=65000.10,
        qty=0.004,
        source="binance.agg_trade",
        seq=10,
        exchange_ts_ns=1_700_000_000_000_000_000,
        received_ts_ns=2_000_000_000_000_000_000,
    )


def test_parse_book_ticker_uses_midpoint_and_minimum_quantity() -> None:
    tick = parse_message(book_ticker(), FixedClock(0, 0))

    assert tick.price == pytest.approx(65000.10)
    assert tick.qty == pytest.approx(0.4)
    assert tick.source == "binance.book_ticker"
    assert tick.seq == 20


@pytest.mark.parametrize(
    "message",
    [
        {"e": "aggTrade", "E": 1, "s": "ETHUSDT", "p": "1", "q": "1", "a": 1},
        {"e": "aggTrade", "E": 1, "s": "BTCUSDT", "p": "0", "q": "1", "a": 1},
        {"e": "aggTrade", "E": 1, "s": "BTCUSDT", "p": "1", "q": "-1", "a": 1},
    ],
)
def test_invalid_messages_are_rejected(message: dict) -> None:
    with pytest.raises(BinanceMessageError):
        parse_message(json.dumps(message), FixedClock(0, 0))


def test_reader_tracks_sequences_and_rejects_duplicate() -> None:
    reader = BinancePriceReader(queue=asyncio.Queue(maxsize=2), clock=FixedClock(0, 0))

    assert reader.feed(agg_trade(10)) is not None
    assert reader.feed(agg_trade(10)) is None
    assert reader.metrics.sequence_gap_count == 1
    assert reader.metrics.tick_count == 1


def test_reader_uses_independent_sequences_for_each_stream() -> None:
    reader = BinancePriceReader(queue=asyncio.Queue(maxsize=3), clock=FixedClock(0, 0))

    assert reader.feed(agg_trade(10)) is not None
    assert reader.feed(book_ticker(20)) is not None
    assert reader.metrics.tick_count == 2


def test_reader_reports_bounded_queue_overflow() -> None:
    reader = BinancePriceReader(queue=asyncio.Queue(maxsize=1), clock=FixedClock(0, 0))

    reader.feed(agg_trade(10))
    with pytest.raises(QueueOverflowError):
        reader.feed(agg_trade(11))
    assert reader.metrics.queue_overflow_count == 1


class FakeTransport:
    def __init__(self, messages: list[str], on_exhausted=None) -> None:
        self.messages = messages
        self.on_exhausted = on_exhausted

    def __aiter__(self):
        self._iterator = iter(self.messages)
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._iterator)
        except StopIteration as exc:
            if self.on_exhausted is not None:
                self.on_exhausted()
            raise StopAsyncIteration from exc

    async def aclose(self) -> None:
        return None


def test_reader_reconnects_after_transport_closes() -> None:
    calls = 0

    async def factory() -> FakeTransport:
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeTransport([])
        return FakeTransport([agg_trade(11)], on_exhausted=reader.stop)

    reader = BinancePriceReader(
        queue=asyncio.Queue(maxsize=2),
        clock=FixedClock(0, 0),
        transport_factory=factory,
        reconnect_initial_delay=0,
        reconnect_max_delay=0,
    )
    asyncio.run(reader.run())

    assert calls == 2
    assert reader.metrics.reconnect_count == 2
    assert reader.metrics.tick_count == 1
