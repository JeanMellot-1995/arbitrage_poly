import pytest

from arbitrage_poly.pricing.sizing import SizingConfig, compute_sizing


def test_rejected_opportunity_returns_zero_without_evaluating_kelly() -> None:
    config = SizingConfig(mode="dynamic", bankroll=1000.0)

    decision = compute_sizing(accepted=False, config=config, probability=0.9, price=0.1)

    assert decision.stake_usd == 0.0
    assert decision.reason == "opportunity_rejected"
    assert decision.kelly_fraction is None


def test_kill_switch_returns_zero_even_with_a_strong_edge() -> None:
    config = SizingConfig(mode="dynamic", bankroll=1000.0, kill_switch_active=True)

    decision = compute_sizing(accepted=True, config=config, probability=0.9, price=0.1)

    assert decision.stake_usd == 0.0
    assert decision.reason == "kill_switch_active"
    assert decision.kelly_fraction is None


def test_fixed_mode_uses_configured_stake() -> None:
    config = SizingConfig(mode="fixed", fixed_stake_usd=10.0, max_stake_per_market=20.0)

    decision = compute_sizing(accepted=True, config=config)

    assert decision.stake_usd == 10.0
    assert decision.mode == "fixed"
    assert decision.applied_cap is None
    assert decision.reason is None


def test_fixed_mode_is_capped_by_max_stake_per_market() -> None:
    config = SizingConfig(mode="fixed", fixed_stake_usd=50.0, max_stake_per_market=20.0)

    decision = compute_sizing(accepted=True, config=config)

    assert decision.stake_usd == 20.0
    assert decision.applied_cap == "max_stake_per_market"


def test_dynamic_mode_is_capped_by_kelly_fraction_cap() -> None:
    config = SizingConfig(
        mode="dynamic", bankroll=10.0, kelly_fraction_cap=0.5, max_stake_per_market=20.0
    )

    # b = (1 - 0.5) / 0.5 = 1.0 ; kelly_fraction = (0.7 * 2 - 1) / 1.0 = 0.4
    decision = compute_sizing(accepted=True, config=config, probability=0.7, price=0.5)

    assert decision.kelly_fraction == pytest.approx(0.4)
    assert decision.stake_usd == pytest.approx(2.0)
    assert decision.applied_cap == "kelly_fraction_cap"


def test_dynamic_mode_is_capped_by_max_stake_per_market() -> None:
    config = SizingConfig(
        mode="dynamic", bankroll=1000.0, kelly_fraction_cap=0.5, max_stake_per_market=20.0
    )

    decision = compute_sizing(accepted=True, config=config, probability=0.7, price=0.5)

    assert decision.stake_usd == 20.0
    assert decision.applied_cap == "max_stake_per_market"


def test_dynamic_mode_below_min_stake_returns_zero_but_reports_kelly_fraction() -> None:
    config = SizingConfig(mode="dynamic", bankroll=1.0, kelly_fraction_cap=0.5, min_stake_usd=1.0)

    decision = compute_sizing(accepted=True, config=config, probability=0.51, price=0.5)

    assert decision.stake_usd == 0.0
    assert decision.reason == "below_min_stake"
    assert decision.kelly_fraction is not None
    assert decision.kelly_fraction > 0.0


def test_dynamic_mode_zero_edge_returns_zero_stake() -> None:
    config = SizingConfig(mode="dynamic", bankroll=1000.0)

    decision = compute_sizing(accepted=True, config=config, probability=0.5, price=0.5)

    assert decision.kelly_fraction == 0.0
    assert decision.stake_usd == 0.0
    assert decision.reason == "below_min_stake"


def test_dynamic_mode_rounds_stake_down_to_tick_size() -> None:
    config = SizingConfig(mode="dynamic", bankroll=1000.0, kelly_fraction_cap=0.5, tick_size=0.01)

    decision = compute_sizing(accepted=True, config=config, probability=0.61, price=0.5)

    assert round(decision.stake_usd, 2) == decision.stake_usd


def test_dynamic_mode_requires_probability_and_price() -> None:
    config = SizingConfig(mode="dynamic", bankroll=1000.0)

    with pytest.raises(ValueError):
        compute_sizing(accepted=True, config=config)


def test_dynamic_mode_rejects_invalid_probability() -> None:
    config = SizingConfig(mode="dynamic", bankroll=1000.0)

    with pytest.raises(ValueError):
        compute_sizing(accepted=True, config=config, probability=1.5, price=0.5)


def test_dynamic_mode_rejects_invalid_price() -> None:
    config = SizingConfig(mode="dynamic", bankroll=1000.0)

    with pytest.raises(ValueError):
        compute_sizing(accepted=True, config=config, probability=0.7, price=0.0)


def test_fixed_mode_requires_fixed_stake_usd() -> None:
    with pytest.raises(ValueError):
        SizingConfig(mode="fixed")


def test_dynamic_mode_requires_bankroll() -> None:
    with pytest.raises(ValueError):
        SizingConfig(mode="dynamic")


def test_kelly_fraction_cap_must_be_in_bounds() -> None:
    with pytest.raises(ValueError):
        SizingConfig(mode="fixed", fixed_stake_usd=10.0, kelly_fraction_cap=0.0)
    with pytest.raises(ValueError):
        SizingConfig(mode="fixed", fixed_stake_usd=10.0, kelly_fraction_cap=1.5)


def test_min_stake_usd_cannot_exceed_max_stake_per_market() -> None:
    with pytest.raises(ValueError):
        SizingConfig(
            mode="fixed", fixed_stake_usd=10.0, min_stake_usd=30.0, max_stake_per_market=20.0
        )
