"""A load's power sensor that lags the grid power: a start or a stop is not a house step."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .conftest import GRID, IMPORT, SOURCES, set_sources, setup_entry, tick, value

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

PLUGGED_IN = "binary_sensor.ev_plugged_in"


async def _meter(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: str, grid_w: float
) -> None:
    """The P1 meter reports; the load's sensor does not."""
    freezer.move_to(at)
    hass.states.async_set(GRID, str(grid_w), {"unit_of_measurement": "W"})
    hass.states.async_set(IMPORT, "100.0", {"unit_of_measurement": "kWh"})
    await tick(hass)


async def _start(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ev_w: float, plugged_in: str = ""
) -> None:
    """House 1000 W and the EV at ``ev_w``, 1 s into the 03:15 window.

    ``plugged_in`` is the state of the EV's demand entity; without one, the EV has none.
    """
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000 + ev_w, 100.0, ev_w)
    if plugged_in:
        hass.states.async_set(PLUGGED_IN, plugged_in)
    await setup_entry(hass, {**SOURCES, "demand": PLUGGED_IN} if plugged_in else None)
    freezer.move_to("2026-11-15T03:15:01+00:00")
    set_sources(hass, 1000 + ev_w, 100.0, ev_w)
    await tick(hass)
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)


async def test_a_start_is_not_a_house_step(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The grid steps up 4 kW; the EV's 30 s sensor follows 25 s later."""
    await _start(hass, freezer, 0)
    assert value(hass, "mode") == "continuous"
    await _meter(hass, freezer, "2026-11-15T03:15:06+00:00", 5140)
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)
    assert value(hass, "mode") == "continuous"
    assert float(value(hass, "power")) == pytest.approx(4140, abs=10)
    freezer.move_to("2026-11-15T03:15:31+00:00")
    set_sources(hass, 5140, 100.0, 4140)
    await tick(hass)
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)


async def test_more_than_the_load_may_add_is_house(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Cooking starts with the EV: what the EV may not draw is the house's."""
    await _start(hass, freezer, 0)
    allowed = float(value(hass, "allowed_power"))
    await _meter(hass, freezer, "2026-11-15T03:15:06+00:00", 1000 + allowed + 2000)
    assert float(value(hass, "house_power")) == pytest.approx(3000, abs=10)


async def test_a_rise_below_an_idle_load_s_minimum_is_house(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The EV starts at 6 A (~4.1 kW) at least: a 1.5 kW rise while it is idle is cooking."""
    await _start(hass, freezer, 0)
    await _meter(hass, freezer, "2026-11-15T03:15:06+00:00", 2500)
    assert float(value(hass, "house_power")) == pytest.approx(2500, abs=10)
    assert float(value(hass, "power")) == 0


async def test_a_stop_is_not_a_house_drop(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The EV stops; its sensor still says 4140 W. The house must not drop to 0 W."""
    await _start(hass, freezer, 4140)
    await _meter(hass, freezer, "2026-11-15T03:15:06+00:00", 1000)
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)
    assert float(value(hass, "power")) == 0


async def test_a_sensor_that_reports_the_same_value_lets_the_house_follow(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The house drops 1 kW while the EV keeps reporting 4140 W: only last_reported moves."""
    await _start(hass, freezer, 4140)
    await _meter(hass, freezer, "2026-11-15T03:15:06+00:00", 4140)
    # Held until the EV reports again: the safe side.
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)
    freezer.move_to("2026-11-15T03:15:31+00:00")
    set_sources(hass, 4140, 100.0, 4140)
    await tick(hass)
    assert float(value(hass, "house_power")) == pytest.approx(0, abs=10)


async def _silent_idle(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """The EV's sensor writes only on a change: silent at 0 W for ten minutes.

    Without a demand entity, so its claim has long lapsed: it can still start.
    """
    await _start(hass, freezer, 0)
    for minute in range(16, 26):
        await _meter(hass, freezer, f"2026-11-15T03:{minute}:00+00:00", 1000)
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)


async def test_a_start_after_a_long_silence_is_not_a_house_step(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _silent_idle(hass, freezer)
    await _meter(hass, freezer, "2026-11-15T03:25:10+00:00", 5140)
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)
    assert value(hass, "mode") == "continuous"
    await _meter(hass, freezer, "2026-11-15T03:25:50+00:00", 5140)
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)


async def test_a_sensor_slower_than_the_lag_limit_counts_as_house(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The documented limit: 45 s after the step, an unexplained rise is the house's."""
    await _silent_idle(hass, freezer)
    await _meter(hass, freezer, "2026-11-15T03:25:10+00:00", 5140)
    await _meter(hass, freezer, "2026-11-15T03:26:00+00:00", 5140)
    assert float(value(hass, "house_power")) == pytest.approx(5140, abs=10)


async def test_an_idle_load_that_cannot_start_takes_no_rise(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """No car plugged in: an oven, a kettle and a hob are the house's, not the EV's."""
    await _start(hass, freezer, 0, plugged_in="off")
    await _meter(hass, freezer, "2026-11-15T03:15:06+00:00", 3500)
    assert float(value(hass, "house_power")) == pytest.approx(3500, abs=10)
    assert float(value(hass, "power")) == 0


async def test_after_a_meter_outage_a_step_is_the_house_s(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The grid before the outage is minutes old: no load's start to pair it with."""
    await _start(hass, freezer, 0, plugged_in="on")
    freezer.move_to("2026-11-15T03:20:00+00:00")
    await tick(hass)
    assert value(hass, "mode") == "fallback"
    await _meter(hass, freezer, "2026-11-15T03:20:05+00:00", 4000)
    assert float(value(hass, "house_power")) == pytest.approx(4000, abs=10)


async def test_an_idle_load_that_wants_power_takes_its_start(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Car plugged in: the 4 kW step is the EV's start, not the house's."""
    await _start(hass, freezer, 0, plugged_in="on")
    await _meter(hass, freezer, "2026-11-15T03:15:06+00:00", 5140)
    assert float(value(hass, "house_power")) == pytest.approx(1000, abs=10)
    assert float(value(hass, "power")) == pytest.approx(4140, abs=10)
