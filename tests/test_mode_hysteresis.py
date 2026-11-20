"""The load's mode does not flip on every evaluation around the continuous boundary."""

from __future__ import annotations

import itertools
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .conftest import GRID, IMPORT, set_sources, setup_entry, tick, value

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

GOAL = 5400.0
P_MIN = 4140.0


async def test_a_house_around_the_boundary_changes_the_mode_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The house swings 50 W around the continuous boundary every 5 s, for a minute."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass)
    start = dt_util.parse_datetime("2026-11-15T03:15:00+00:00")
    assert start is not None
    modes = []
    for k in range(1, 13):
        t = 5 * k
        freezer.move_to(start + timedelta(seconds=t))
        # Nothing used yet: the budget's average power over the rest of the window.
        boundary = GOAL * 900 / (900 - t) - P_MIN
        house = boundary - 50 if k % 2 else boundary + 50
        hass.states.async_set(GRID, str(house), {"unit_of_measurement": "W"})
        hass.states.async_set(IMPORT, "100.0", {"unit_of_measurement": "kWh"})
        await tick(hass)
        modes.append(value(hass, "mode"))
    changes = sum(a != b for a, b in itertools.pairwise(modes))
    assert modes[0] == "continuous"
    assert changes == 1
