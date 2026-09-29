"""Reference prices for recurring binary-market windows."""

from __future__ import annotations

from dataclasses import dataclass

from arbitrage_poly.models import Tick

DEFAULT_WINDOW_NS = 5 * 60 * 1_000_000_000

def window_start_ns(ts_ns: int, window_ns: int = DEFAULT_WINDOW_NS) -> int:
    """Return the UTC-aligned start of the window containing ``ts_ns``."""

    if ts_ns <= 0 or window_ns <= 0:
        raise ValueError("timestamps and window size must be positive")
    return (ts_ns // window_ns) * window_ns


@dataclass(frozen=True, slots=True)
class WindowReference:
    window_start_ns: int
    price: float


class ReferenceTracker:
    """Use the first observed tick in each window as its reference price."""

    def __init__(self, window_ns: int = DEFAULT_WINDOW_NS) -> None:
        if window_ns <= 0:
            raise ValueError("window_ns must be positive")
        self.window_ns = window_ns
        self._references: dict[int, WindowReference] = {}

    def observe(self, tick: Tick) -> WindowReference:
        start_ns = window_start_ns(tick.ts_ns, self.window_ns)
        reference = self._references.get(start_ns)
        if reference is None:
            reference = WindowReference(start_ns, tick.price)
            self._references[start_ns] = reference
        return reference
