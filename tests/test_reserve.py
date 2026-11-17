"""The reserve of the loads that are only measured: their share of what remains."""

from __future__ import annotations

import pytest

from custom_components.peak_basket.model import measured_reserves


def test_each_measured_load_reserves_its_power_until_the_window_ends() -> None:
    # 300 s left: 1800 W is 150 Wh, 600 W is 50 Wh.
    assert measured_reserves(
        remaining_wh=1000, house_reserve_wh=50, powers_w=[1800, 600], rest_s=300
    ) == pytest.approx([150, 50])


def test_the_reserves_never_take_more_than_the_house_leaves() -> None:
    # 120 Wh remain, the house keeps 50: 70 Wh for the first, nothing for the second.
    assert measured_reserves(
        remaining_wh=120, house_reserve_wh=50, powers_w=[1800, 600], rest_s=300
    ) == pytest.approx([70, 0])


def test_over_budget_nothing_is_reserved() -> None:
    assert measured_reserves(remaining_wh=-20, house_reserve_wh=0, powers_w=[1800], rest_s=300) == [
        0
    ]


def test_without_a_remaining_budget_the_reserves_are_unknown() -> None:
    assert measured_reserves(
        remaining_wh=None, house_reserve_wh=None, powers_w=[1800], rest_s=300
    ) == [None]
