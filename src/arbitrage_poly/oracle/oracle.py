"""Terminal probability Oracle built from normalized market-data ticks."""

from __future__ import annotations

from arbitrage_poly.models import FairValue, Tick

from .probability import MODEL_NAME, terminal_prob_up
from .reference import DEFAULT_WINDOW_NS, ReferenceTracker
from .volatility import EwmaVolatility, HorizonEwmaVolatility


class PriceOracle:
    """Produce a terminal UP/DOWN fair value without performing I/O."""

    def __init__(
        self,
        *,
        window_ns: int = DEFAULT_WINDOW_NS,
        source: str = "binance.book_ticker",
        model: str = MODEL_NAME,
        volatility_alpha: float = 0.1,
        volatility_floor: float = 1e-6,
        volatility_sampling_interval_ns: int | None = None,
        probability_floor: float = 0.05,
        probability_ceiling: float = 0.95,
        reversion_speed: float = 0.0,
        volatility_estimator: EwmaVolatility | HorizonEwmaVolatility | None = None,
    ) -> None:
        self._latest: dict[str, Tick] = {}
        self.source = source
        self.model = model
        self.window_ns = window_ns
        if not 0.0 <= probability_floor < probability_ceiling <= 1.0:
            raise ValueError("probability bounds must satisfy 0 <= floor < ceiling <= 1")
        self.probability_floor = probability_floor
        self.probability_ceiling = probability_ceiling
        self.reversion_speed = reversion_speed
        self._references = ReferenceTracker(window_ns)
        self._volatility = volatility_estimator or EwmaVolatility(
            alpha=volatility_alpha,
            floor=volatility_floor,
            sampling_interval_ns=volatility_sampling_interval_ns,
        )

    def observe(self, tick: Tick) -> FairValue | None:
        """Accept a tick and return its fair value when it is the configured source."""

        self._latest[tick.source] = tick
        if tick.source != self.source:
            return None

        reference = self._references.observe(tick)
        volatility = self._volatility.update(tick)
        window_end_ns = reference.window_start_ns + self.window_ns
        remaining_ns = max(0, window_end_ns - tick.ts_ns)
        prob_up_raw = terminal_prob_up(
            current_price=tick.price,
            reference_price=reference.price,
            remaining_ns=remaining_ns,
            volatility=volatility,
            reversion_speed=self.reversion_speed,
        )
        prob_up = min(self.probability_ceiling, max(self.probability_floor, prob_up_raw))
        return FairValue(
            ts_ns=tick.ts_ns,
            window_start_ns=reference.window_start_ns,
            reference_price=reference.price,
            current_price=tick.price,
            prob_up=prob_up,
            prob_down=1.0 - prob_up,
            model=self.model,
            volatility=volatility,
            remaining_ns=remaining_ns,
            prob_up_raw=prob_up_raw,
        )

    def latest(self, source: str) -> Tick | None:
        """Return the latest tick for a source, if one has been observed."""

        return self._latest.get(source)

    @property
    def volatility_ready(self) -> bool:
        return self._volatility.ready
