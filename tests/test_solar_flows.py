"""Solar and grid energy per window, attributed to the loads first, then the house."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er

from custom_components.peak_basket.const import DOMAIN

from .conftest import EV, GRID, SOURCES, setup_entry, tick, value

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

PV = "sensor.pv_power"
AVERAGE = "sensor.p1_average"


def _report(hass: HomeAssistant, grid: float, pv: float | str, ev: float) -> None:
    hass.states.async_set(GRID, str(grid), {"unit_of_measurement": "W"})
    hass.states.async_set(PV, str(pv), {"unit_of_measurement": "W"})
    hass.states.async_set(EV, str(ev), {"unit_of_measurement": "W"})
    hass.states.async_set(AVERAGE, "0", {"unit_of_measurement": "W"})


async def _setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-11-15T12:15:01+00:00")
    _report(hass, -500, 3000, 2000)
    sources: dict[str, Any] = {**SOURCES, "window_average": AVERAGE, "pv_power": PV}
    del sources["grid_import_energy"]
    await setup_entry(hass, sources)


async def _after(hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: str) -> None:
    freezer.move_to(at)
    await tick(hass)


async def test_solar_goes_to_the_load_first(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, freezer)
    # 36 s = 0.01 h of: PV 3000 W, export 500 W, EV 2000 W, so 2500 W used on the spot.
    _report(hass, -500, 3000, 2000)
    await _after(hass, freezer, "2026-11-15T12:15:37+00:00")
    assert float(value(hass, "solar")) == 30
    assert float(value(hass, "export")) == 5
    assert float(value(hass, "house_solar")) == 5
    assert float(value(hass, "house_grid")) == 0
    assert float(value(hass, "load_solar")) == 20
    assert float(value(hass, "load_grid")) == 0


async def _at(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: str, *report: float | str
) -> None:
    """Move to ``at``, report grid, PV and EV power then, and evaluate."""
    freezer.move_to(at)
    grid, pv, ev = report
    _report(hass, float(grid), pv, float(ev))
    await tick(hass)


async def test_grid_import_is_split_over_load_and_house(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, freezer)
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", 4000, 1000, 3000)
    # 36 s of a 2000 W house: the EV takes the 1000 W solar first, the grid the rest.
    await _at(hass, freezer, "2026-11-15T12:15:37+00:00", 4000, 1000, 3000)
    assert float(value(hass, "load_solar")) == 10
    assert float(value(hass, "load_grid")) == 20
    assert float(value(hass, "house_grid")) == 20
    assert float(value(hass, "house_solar")) == 0


async def test_a_new_window_starts_from_zero(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, freezer)
    _report(hass, -500, 3000, 2000)
    await _after(hass, freezer, "2026-11-15T12:15:37+00:00")
    assert float(value(hass, "solar")) == 30
    _report(hass, -500, 3000, 2000)
    await _after(hass, freezer, "2026-11-15T12:30:01+00:00")
    assert float(value(hass, "solar")) == 0


async def test_gaps_and_unknown_sources_add_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, freezer)
    # The inverter sleeps: an interval that starts without PV adds nothing.
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", -500, "unavailable", 2000)
    await _at(hass, freezer, "2026-11-15T12:15:37+00:00", -500, 3000, 2000)
    assert float(value(hass, "solar")) == 0
    # A gap longer than the maximum source age is not filled in.
    await _at(hass, freezer, "2026-11-15T12:17:37+00:00", -500, 3000, 2000)
    assert float(value(hass, "solar")) == 0
    await _at(hass, freezer, "2026-11-15T12:18:13+00:00", -500, 3000, 2000)
    assert float(value(hass, "solar")) == 30


async def test_no_solar_sensors_without_a_pv_source(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-15T12:15:01+00:00")
    _report(hass, 500, 0, 0)
    hass.states.async_set("sensor.p1_import", "1", {"unit_of_measurement": "kWh"})
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_solar") is None
    assert entry.runtime_data.data.flows is None
