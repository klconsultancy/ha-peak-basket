"""The load's mode does not flip on every evaluation around a boundary."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.util import dt as dt_util

from .conftest import AVERAGE, GRID, IMPORT, SOURCES, set_sources, setup_entry, tick, value

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

GOAL = 5400.0
P_MIN = 4140.0
# The load's minimum plus the pulse headroom: what pulse on gives, continuous at least.
P_FLOOR = P_MIN + 200


async def test_around_the_continuous_boundary_the_load_keeps_at_least_the_pulse_power(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The house swings 50 W around the continuous boundary every 5 s, for a minute.

    On the boundary continuous's limit is pulse on's, so the mode may follow the house but
    the load's room only moves with it: never below its minimum plus the pulse headroom,
    the power EVCC needs to hold the charger (#91).
    """
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass)
    start = dt_util.parse_datetime("2026-11-15T03:15:00+00:00")
    assert start is not None
    allowed = []
    for k in range(1, 13):
        t = 5 * k
        freezer.move_to(start + timedelta(seconds=t))
        # Nothing used yet: the budget's average power over the rest of the window.
        boundary = GOAL * 900 / (900 - t) - P_FLOOR
        house = boundary - 50 if k % 2 else boundary + 50
        hass.states.async_set(GRID, str(house), {"unit_of_measurement": "W"})
        hass.states.async_set(IMPORT, "100.0", {"unit_of_measurement": "kWh"})
        await tick(hass)
        allowed.append(float(value(hass, "allowed_power")))
    assert min(allowed) == pytest.approx(P_FLOOR, abs=1)
    assert max(allowed) == pytest.approx(P_FLOOR + 50, abs=1)


# An idle load may start with budget for 4140 W over the reaction and minimum run time.
START_HOUSE = GOAL - P_MIN * (90 + 300) / 900


async def test_an_idle_load_around_the_start_changes_the_mode_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The house swings 120 W around an idle load's start every 5 s, for a minute.

    As the oven did in the 17:30 window of #88, at the start of the window: 80 W below the
    start over the 900 s left moves the load's room by 20 Wh.

    As a HomeWizard writes it: the power first, then the window average a moment later.
    The grid power evaluates on the old average, so it sees 5 s of import too little.
    """
    sources = {**SOURCES, "window_average": AVERAGE}
    del sources["grid_import_energy"]
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, START_HOUSE + 40, 100.0, 0)
    hass.states.async_set(AVERAGE, "0", {"unit_of_measurement": "W"})
    await setup_entry(hass, sources)
    start = dt_util.parse_datetime("2026-11-15T03:15:00+00:00")
    assert start is not None
    used_j = 0.0
    house = START_HOUSE + 40
    modes = []
    for k in range(1, 13):
        used_j += house * 5
        house = START_HOUSE + 40 if k % 2 else START_HOUSE - 80
        freezer.move_to(start + timedelta(seconds=5 * k))
        hass.states.async_set(GRID, str(house), {"unit_of_measurement": "W"})
        await hass.async_block_till_done()
        modes.append(value(hass, "mode"))
        freezer.tick(0.006)
        hass.states.async_set(AVERAGE, str(used_j / 900), {"unit_of_measurement": "W"})
        await tick(hass)
        modes.append(value(hass, "mode"))
    # The window's first evaluation leaves continuous; nothing after that starts the load.
    assert modes[0] == "continuous"
    assert modes[1:] == ["pulse_off"] * 23


@pytest.mark.parametrize("energy", ["average", "counter"])
async def test_the_used_energy_follows_the_grid_between_meter_reports(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, energy: str
) -> None:
    """The meter's energy reports every 5 s, or less often: in between the grid imports."""
    sources = {**SOURCES, "window_average": AVERAGE} if energy == "average" else SOURCES
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 3600, 100.0, 0)
    hass.states.async_set(AVERAGE, "0", {"unit_of_measurement": "W"})
    await setup_entry(hass, sources)
    freezer.move_to("2026-11-15T03:15:20+00:00")
    set_sources(hass, 3600, 100.02, 0)
    hass.states.async_set(AVERAGE, "80", {"unit_of_measurement": "W"})  # 20 Wh
    await tick(hass)
    assert float(value(hass, "used")) == pytest.approx(20, abs=0.5)
    freezer.move_to("2026-11-15T03:15:30+00:00")
    await tick(hass)
    assert float(value(hass, "used")) == pytest.approx(30, abs=0.5)
