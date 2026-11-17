"""The reserve of the loads that are only measured: their share of what remains."""

from __future__ import annotations

import pytest

from custom_components.peak_basket.model import Mode, ModelResult, measured_reserves


def _basket(remaining_wh: float | None, house_reserve_wh: float | None) -> ModelResult:
    """The connection's basket: only what remains and the house reserve matter here."""
    return ModelResult(
        mode=Mode.CONTINUOUS,
        limit_w=0,
        allowed_load_w=0,
        house_w=None,
        charge_minutes=0,
        basket_wh=1375,
        margin_wh=25,
        used_wh=None,
        remaining_wh=remaining_wh,
        house_reserve_wh=house_reserve_wh,
        load_room_wh=None,
        p_load_min_w=0,
        p_load_max_w=0,
    )


def test_each_measured_load_reserves_its_power_until_the_window_ends() -> None:
    # 300 s left: 1800 W is 150 Wh, 600 W is 50 Wh. House 600 W, nothing steered draws.
    assert measured_reserves(
        _basket(1000, 50),
        [1800, 600],
        300,
        3000,
        0,
    ) == pytest.approx([150, 50])


def test_the_reserves_never_take_more_than_the_house_leaves() -> None:
    # 120 Wh remain, the house keeps 50: 70 Wh for the first, nothing for the second.
    assert measured_reserves(
        _basket(120, 50),
        [1800, 600],
        300,
        3000,
        0,
    ) == pytest.approx([70, 0])


def test_over_budget_nothing_is_reserved() -> None:
    assert measured_reserves(_basket(-20, 0), [1800], 300, 1800, 0) == [0]


def test_without_a_remaining_budget_the_reserves_are_unknown() -> None:
    assert measured_reserves(
        _basket(None, None),
        [1800],
        300,
        None,
        0,
    ) == [None]


def test_only_the_grid_share_is_reserved() -> None:
    """Grid 1000 W, heat pump 2000 W: solar covers half of it, the house is 0."""
    assert measured_reserves(_basket(1000, 0), [2000], 360, 1000, 0) == pytest.approx([100])


def test_solar_covering_everything_reserves_nothing() -> None:
    assert measured_reserves(_basket(1000, 0), [2000], 360, -500, 0) == [0]


def test_the_grid_share_goes_in_priority_order() -> None:
    # Grid 4000 W, a steered EV 1000 W, two heat pumps 2000 W each: 3000 W from the grid
    # for the two of them, so the first gets 2000 W and the second 1000 W.
    assert measured_reserves(
        _basket(1000, 0),
        [2000, 2000],
        360,
        4000,
        1000,
    ) == pytest.approx([200, 100])
