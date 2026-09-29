"""Read-only Binance BTCUSDT ticker collection over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from arbitrage_poly.clock import MonotonicClock
from arbitrage_poly.models import ConnectionState, FlowMetrics, Tick

LOGGER = logging.getLogger(__name__)
BINANCE_WS_URL = "wss://data-stream.binance.vision/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker"
_ALLOWED_STREAMS = {
    "btcusdt@aggtrade": "binance.agg_trade",
    "btcusdt@bookticker": "binance.book_ticker",
}


class MessageTransport(Protocol):
    def __aiter__(self) -> AsyncIterator[str]: ...


class BinanceMessageError(ValueError):
    """Raised when a Binance message cannot become a valid Tick."""


class QueueOverflowError(RuntimeError):
    """Raised when the bounded output queue cannot accept a tick."""


@dataclass(frozen=True, slots=True)
class SequenceAnomaly:
    source: str
    previous: int
    current: int
    kind: str


TransportFactory = Callable[[], Awaitable[MessageTransport]]


def _unwrap_message(raw: str | bytes) -> tuple[str, dict[str, Any]]:
    try:
        message = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BinanceMessageError("invalid JSON message") from exc

    if not isinstance(message, dict):
        raise BinanceMessageError("message must be an object")
    if "stream" in message:
        stream = message.get("stream")
        data = message.get("data")
        if not isinstance(stream, str) or not isinstance(data, dict):
            raise BinanceMessageError("invalid combined stream envelope")
        return stream.lower(), data

    event = message.get("e")
    if event == "aggTrade":
        return "btcusdt@aggtrade", message
    if event == "bookTicker":
        return "btcusdt@bookticker", message
    raise BinanceMessageError("unsupported Binance event")


def _number(data: dict[str, Any], key: str, *, positive: bool = False) -> float:
    value = data.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceMessageError(f"invalid numeric field: {key}") from exc
    if positive and number <= 0:
        raise BinanceMessageError(f"field must be positive: {key}")
    if not positive and number < 0:
        raise BinanceMessageError(f"field must be non-negative: {key}")
    return number


def _timestamp_ns(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value <= 0:
        raise BinanceMessageError(f"invalid timestamp field: {key}")
    return value * 1_000_000


def parse_message(raw: str | bytes, clock: MonotonicClock) -> Tick:
    """Parse one raw Binance event into the shared Tick contract."""

    stream, data = _unwrap_message(raw)
    source = _ALLOWED_STREAMS.get(stream)
    if source is None:
        raise BinanceMessageError(f"unsupported stream: {stream}")
    if data.get("s") not in {None, "BTCUSDT"}:
        raise BinanceMessageError("unsupported symbol")

    exchange_ts_ns = _timestamp_ns(data, "E") if data.get("E") is not None else None
    received_ts_ns = clock.now_ns()
    if source == "binance.agg_trade":
        price = _number(data, "p", positive=True)
        qty = _number(data, "q")
        sequence = data.get("a")
    else:
        bid = _number(data, "b", positive=True)
        ask = _number(data, "a", positive=True)
        if ask < bid:
            raise BinanceMessageError("ask must not be below bid")
        price = (bid + ask) / 2
        qty = min(_number(data, "B"), _number(data, "A"))
        sequence = data.get("u")

    if sequence is not None and (not isinstance(sequence, int) or sequence < 0):
        raise BinanceMessageError("invalid sequence")

    return Tick(
        ts_ns=received_ts_ns,
        price=price,
        qty=qty,
        source=source,
        seq=sequence,
        exchange_ts_ns=exchange_ts_ns,
        received_ts_ns=received_ts_ns,
    )


class BinancePriceReader:
    """Reconnectable, bounded, read-only Binance price reader."""

    def __init__(
        self,
        *,
        queue: asyncio.Queue[Tick] | None = None,
        clock: MonotonicClock | None = None,
        transport_factory: TransportFactory | None = None,
        heartbeat_timeout: float = 30.0,
        reconnect_initial_delay: float = 0.5,
        reconnect_max_delay: float = 30.0,
    ) -> None:
        self.queue = queue or asyncio.Queue(maxsize=10_000)
        self.clock = clock or MonotonicClock.system()
        self.transport_factory = transport_factory or self._connect_default
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.state = ConnectionState()
        self.metrics = FlowMetrics()
        self._last_sequences: dict[str, int] = {}
        self._stop_event = asyncio.Event()

    async def _connect_default(self) -> MessageTransport:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - packaging/environment error
            raise RuntimeError("websockets dependency is required for live reading") from exc
        return await websockets.connect(
            BINANCE_WS_URL,
            ping_interval=20,
            ping_timeout=10,
            open_timeout=10,
        )

    def stop(self) -> None:
        """Request a graceful stop after the current transport operation."""

        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        """Whether the reader has received a stop request."""

        return self._stop_event.is_set()

    def _check_sequence(self, tick: Tick) -> SequenceAnomaly | None:
        if tick.seq is None:
            return None
        previous = self._last_sequences.get(tick.source)
        self._last_sequences[tick.source] = tick.seq
        if previous is None:
            return None
        if tick.seq == previous:
            return SequenceAnomaly(tick.source, previous, tick.seq, "duplicate")
        if tick.seq < previous:
            return SequenceAnomaly(tick.source, previous, tick.seq, "backward")
        if tick.source == "binance.book_ticker":
            return None
        if tick.seq > previous + 1:
            return SequenceAnomaly(tick.source, previous, tick.seq, "gap")
        return None

    def feed(self, raw: str | bytes) -> Tick | None:
        """Parse and enqueue one message; return None for rejected messages."""

        self.metrics.message_count += 1
        try:
            tick = parse_message(raw, self.clock)
        except BinanceMessageError as exc:
            self.metrics.rejected_count += 1
            self.state.last_error = str(exc)
            LOGGER.warning("binance_message_rejected: %s", exc)
            return None

        anomaly = self._check_sequence(tick)
        if anomaly is not None:
            self.metrics.sequence_gap_count += 1
            self.state.sequence_gap_detected = True
            self.state.last_error = f"{anomaly.kind} sequence for {anomaly.source}"
            LOGGER.warning("binance_sequence_anomaly: %s", anomaly)
            return None

        try:
            self.queue.put_nowait(tick)
        except asyncio.QueueFull as exc:
            self.metrics.queue_overflow_count += 1
            self.state.last_error = "output queue is full"
            raise QueueOverflowError("output queue is full") from exc

        now_ns = self.clock.now_ns()
        self.metrics.observe_tick(tick, now_ns)
        self.metrics.queue_depth = self.queue.qsize()
        self.state.last_message_ts_ns = now_ns
        self.state.last_heartbeat_ts_ns = now_ns
        return tick

    async def run(self) -> None:
        """Read until stopped, reconnecting after recoverable failures."""

        delay = self.reconnect_initial_delay
        while not self._stop_event.is_set():
            transport: MessageTransport | None = None
            try:
                transport = await self.transport_factory()
                self._last_sequences.clear()
                self.state.connected = True
                self.state.last_error = None
                self.state.reconnect_attempts = 0
                self.metrics.reconnect_count += 1
                delay = self.reconnect_initial_delay
                iterator = transport.__aiter__()
                while not self._stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(
                            iterator.__anext__(), timeout=self.heartbeat_timeout
                        )
                    except StopAsyncIteration:
                        break
                    self.feed(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.last_error = str(exc)
                LOGGER.warning("binance_transport_error: %s", exc)
            finally:
                self.state.connected = False
                close = getattr(transport, "aclose", None)
                if close is not None:
                    await close()

            if self._stop_event.is_set():
                break
            self.state.reconnect_attempts += 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.reconnect_max_delay)

        self.state.connected = False
