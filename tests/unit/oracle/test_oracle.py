import math

from pytest import approx

from arbitrage_poly.models import Tick
from arbitrage_poly.oracle.oracle import PriceOracle
from arbitrage_poly.oracle.probability import terminal_prob_up
from arbitrage_poly.oracle.volatility import SECONDS_PER_YEAR, EwmaVolatility, HorizonEwmaVolatility

SOURCE = "binance.book_ticker"


def tick(ts_ns: int, price: float) -> Tick:
    return Tick(ts_ns=ts_ns, price=price, qty=1.0, source=SOURCE, seq=None)


def test_oracle_uses_first_tick_as_window_reference() -> None:
    oracle = PriceOracle()

    fair_value = oracle.observe(tick(1_000_000_000, 100.0))

    assert fair_value is not None
    assert fair_value.reference_price == 100.0
    assert fair_value.current_price == 100.0
    assert fair_value.prob_up == 0.5


def test_probability_is_monotonic_and_complementary() -> None:
    lower = terminal_prob_up(
        current_price=99.0,
        reference_price=100.0,
        remaining_ns=60_000_000_000,
        volatility=0.8,
    )
    higher = terminal_prob_up(
        current_price=101.0,
        reference_price=100.0,
        remaining_ns=60_000_000_000,
        volatility=0.8,
    )

    assert lower < 0.5 < higher
    assert lower + (1.0 - lower) == 1.0


def test_probability_is_deterministic_at_expiry() -> None:
    assert terminal_prob_up(
        current_price=101.0,
        reference_price=100.0,
        remaining_ns=0,
        volatility=0.8,
    ) == 1.0
    assert terminal_prob_up(
        current_price=99.0,
        reference_price=100.0,
        remaining_ns=0,
        volatility=0.8,
    ) == 0.0


def test_ewma_uses_one_price_update_per_sampling_interval() -> None:
    estimator = EwmaVolatility(
        alpha=1.0,
        floor=1e-6,
        sampling_interval_ns=1_000_000_000,
    )

    estimator.update(tick(1_100_000_000, 100.0))
    same_interval_volatility = estimator.update(tick(1_900_000_000, 101.0))
    next_interval_volatility = estimator.update(tick(2_100_000_000, 102.0))

    assert same_interval_volatility == 1e-6
    assert next_interval_volatility > same_interval_volatility


def test_oracle_preserves_raw_probability_before_bounding() -> None:
    oracle = PriceOracle(
        probability_floor=0.05,
        probability_ceiling=0.95,
        volatility_floor=1e-6,
        volatility_sampling_interval_ns=1_000_000_000,
    )
    oracle.observe(tick(1_100_000_000, 100.0))
    fair_value = oracle.observe(tick(1_900_000_000, 1_000.0))

    assert fair_value is not None
    assert fair_value.prob_up_raw == 1.0
    assert fair_value.prob_up == 0.95
    assert fair_value.prob_down == approx(0.05)


def test_horizon_ewma_matches_constant_horizon_variance_and_warms_up() -> None:
    estimator = HorizonEwmaVolatility(horizon_s=60, half_life_s=900, warmup_s=600)
    step = 0.001
    # Price toggles every minute, so every 60s log return has magnitude `step`.
    for second in range(1, 700):
        price = 100.0 * math.exp(step if (second // 60) % 2 else 0.0)
        volatility = estimator.update(tick(second * 1_000_000_000, price))
        if second < 61:
            assert volatility == estimator.floor

    assert estimator.ready
    assert volatility == approx(math.sqrt(step**2 / 60 * SECONDS_PER_YEAR))


def test_horizon_ewma_ignores_extra_ticks_in_same_second_and_is_not_ready_early() -> None:
    estimator = HorizonEwmaVolatility(horizon_s=1, half_life_s=10, warmup_s=3600)
    estimator.update(tick(1_000_000_000, 100.0))
    estimator.update(tick(1_500_000_000, 200.0))
    volatility = estimator.update(tick(2_000_000_000, 100.0))

    assert volatility == estimator.floor
    assert not estimator.ready


def test_oracle_accepts_injected_volatility_estimator() -> None:
    estimator = HorizonEwmaVolatility(horizon_s=1, half_life_s=10, warmup_s=0)
    oracle = PriceOracle(volatility_estimator=estimator)
    oracle.observe(tick(1_000_000_000, 100.0))
    fair_value = oracle.observe(tick(2_000_000_000, 100.1))

    assert fair_value is not None
    assert fair_value.volatility == estimator.volatility > estimator.floor
    assert oracle.volatility_ready
