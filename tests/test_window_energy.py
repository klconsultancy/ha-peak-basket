"""Who drew from the basket: the window's energy split over the house and the loads."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.peak_basket.const import DOMAIN

from .conftest import EV, GRID, SOURCES, setup_entry, tick, value

if TYPE_CHECKING:
    import pytest
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

PV = "sensor.pv_power"
AVERAGE = "sensor.p1_average"
EXPORT = "sensor.p1_export"


def _sources(**extra: str) -> dict[str, Any]:
    sources: dict[str, Any] = {**SOURCES, "window_average": AVERAGE, **extra}
    del sources["grid_import_energy"]
    return sources


async def _at(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    at: str,
    *,
    grid: float,
    ev: float,
    average: float = 0,
    pv: float | str | None = None,
    export_kwh: float | None = None,
) -> None:
    """Move to ``at``, report the sources then, and evaluate."""
    freezer.move_to(at)
    hass.states.async_set(GRID, str(grid), {"unit_of_measurement": "W"})
    hass.states.async_set(EV, str(ev), {"unit_of_measurement": "W"})
    hass.states.async_set(AVERAGE, str(average), {"unit_of_measurement": "W"})
    if pv is not None:
        hass.states.async_set(PV, str(pv), {"unit_of_measurement": "W"})
    if export_kwh is not None:
        hass.states.async_set(EXPORT, str(export_kwh), {"unit_of_measurement": "kWh"})
    await tick(hass)


def _exists(hass: HomeAssistant, key: str) -> bool:
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    ids = [f"{entry.entry_id}_{key}"] + [f"{entry.entry_id}_{s}_{key}" for s in entry.subentries]
    registry = er.async_get(hass)
    return any(registry.async_get_entity_id("sensor", DOMAIN, i) for i in ids)


async def test_without_pv_house_and_load_add_up_to_the_import(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _at(hass, freezer, "2026-11-15T03:15:01+00:00", grid=6000, ev=4000)
    await setup_entry(hass, _sources())
    # 36 s = 0.01 h: the EV drew 40 Wh from the grid. The meter says 60 Wh were imported
    # (an average of 240 W over 900 s): the house is the rest.
    await _at(hass, freezer, "2026-11-15T03:15:37+00:00", grid=6000, ev=4000, average=240)
    assert float(value(hass, "used")) == 60
    assert float(value(hass, "load_grid")) == 40
    assert float(value(hass, "house_grid")) == 20
    for key in ("solar", "house_solar", "load_solar", "export"):
        assert not _exists(hass, key)


async def test_house_never_goes_below_zero(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The meter and the load's sensor do not tick together: the house takes no blame."""
    await _at(hass, freezer, "2026-11-15T03:15:01+00:00", grid=6000, ev=4000)
    await setup_entry(hass, _sources())
    await _at(hass, freezer, "2026-11-15T03:15:37+00:00", grid=6000, ev=4000, average=100)
    assert float(value(hass, "load_grid")) == 40
    assert float(value(hass, "house_grid")) == 0


async def test_without_the_import_the_house_is_unknown(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _at(hass, freezer, "2026-11-15T03:15:01+00:00", grid=6000, ev=4000)
    await setup_entry(hass, _sources())
    await _at(hass, freezer, "2026-11-15T03:15:37+00:00", grid=6000, ev=4000, average=240)
    hass.states.async_set(AVERAGE, "unavailable")
    await tick(hass)
    assert value(hass, "house_grid") == "unknown"
    assert float(value(hass, "load_grid")) == 40


async def test_solar_goes_to_the_load_first(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", grid=-500, ev=2000, pv=3000)
    await setup_entry(hass, _sources(pv_power=PV))
    # PV 3000 W, export 500 W, EV 2000 W: 2500 W used on the spot, the EV first.
    await _at(hass, freezer, "2026-11-15T12:15:37+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 30
    assert float(value(hass, "export")) == 5
    assert float(value(hass, "load_solar")) == 20
    assert float(value(hass, "house_solar")) == 5
    assert float(value(hass, "load_grid")) == 0
    assert float(value(hass, "house_grid")) == 0


async def test_solar_and_grid_together(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", grid=4000, ev=3000, pv=1000)
    await setup_entry(hass, _sources(pv_power=PV))
    # House 2000 W: the EV takes the 1000 W solar first, the grid the rest.
    await _at(hass, freezer, "2026-11-15T12:15:37+00:00", grid=4000, ev=3000, pv=1000, average=160)
    assert float(value(hass, "load_solar")) == 10
    assert float(value(hass, "load_grid")) == 20
    assert float(value(hass, "house_grid")) == 20
    assert float(value(hass, "house_solar")) == 0


async def test_export_counter_gives_the_export(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _at(hass, freezer, "2026-11-15T12:14:59+00:00", grid=-500, ev=0, pv=2000, export_kwh=10.0)
    await setup_entry(hass, _sources(pv_power=PV, grid_export_energy=EXPORT))
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", grid=-500, ev=0, pv=2000, export_kwh=10.0)
    # The register counts from the last reading before the boundary, like the import.
    await _at(
        hass, freezer, "2026-11-15T12:15:37+00:00", grid=-500, ev=0, pv=2000, export_kwh=10.012
    )
    assert float(value(hass, "export")) == 12
    # PV 2000 W for 37 s (1 s after the boundary, then 36 s) = 20.6 Wh, of which 12 Wh
    # exported: the house used 8.6 Wh of it.
    assert float(value(hass, "house_solar")) == 9


async def test_export_counter_without_pv(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A home battery can export without a PV source configured."""
    await _at(hass, freezer, "2026-11-15T12:14:59+00:00", grid=-500, ev=0, export_kwh=10.0)
    await setup_entry(hass, _sources(grid_export_energy=EXPORT))
    await _at(hass, freezer, "2026-11-15T12:15:37+00:00", grid=-500, ev=0, export_kwh=10.005)
    assert float(value(hass, "export")) == 5
    assert not _exists(hass, "solar")


async def test_a_new_window_starts_from_zero(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", grid=-500, ev=2000, pv=3000)
    await setup_entry(hass, _sources(pv_power=PV))
    await _at(hass, freezer, "2026-11-15T12:15:37+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 30
    await _at(hass, freezer, "2026-11-15T12:30:01+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 0
    assert float(value(hass, "load_solar")) == 0


async def test_gaps_and_unknown_sources_add_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", grid=-500, ev=2000, pv=3000)
    await setup_entry(hass, _sources(pv_power=PV))
    # The inverter sleeps: an interval that starts without PV adds nothing.
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", grid=-500, ev=2000, pv="unavailable")
    await _at(hass, freezer, "2026-11-15T12:15:37+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 0
    # A gap longer than the maximum source age is not filled in.
    await _at(hass, freezer, "2026-11-15T12:17:37+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 0
    await _at(hass, freezer, "2026-11-15T12:18:13+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 30


def _entry_with_pv(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Peak Basket",
        version=2,
        data={
            "profile": "be_flanders",
            "grid_power": GRID,
            "window_average": AVERAGE,
            "pv_power": PV,
        },
        options={"peak_goal_w": 5500.0, "margin_w": 100.0, "max_age_s": 60.0},
        subentries_data=[
            {
                "data": {"load_power": EV},
                "subentry_id": "ev",
                "subentry_type": "load",
                "title": "EV charger",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _restore(
    hass: HomeAssistant, entry: MockConfigEntry, saved: dict[str, str], at: datetime
) -> None:
    registry = er.async_get(hass)
    states = []
    for key, saved_value in saved.items():
        unique_id = f"{entry.entry_id}_{key}"
        subentry = None
        if key.startswith("load_"):
            unique_id = f"{entry.entry_id}_ev_{key}"
            subentry = "ev"
        entity_id = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            config_entry=entry,
            config_subentry_id=subentry,
            suggested_object_id=key,
        ).entity_id
        state = State(entity_id, saved_value, last_updated=at, last_changed=at)
        states.append(state)
    mock_restore_cache(hass, states)


async def test_restart_mid_window_keeps_the_window_so_far(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = _entry_with_pv(hass)
    saved_at = datetime(2026, 11, 15, 12, 20, tzinfo=UTC)
    _restore(hass, entry, {"solar": "300", "load_solar": "200", "load_grid": "50"}, saved_at)
    await _at(hass, freezer, "2026-11-15T12:21:00+00:00", grid=-500, ev=2000, pv=3000)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _at(hass, freezer, "2026-11-15T12:21:36+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 330
    assert float(value(hass, "load_solar")) == 220
    assert float(value(hass, "load_grid")) == 50


async def test_restart_does_not_restore_an_earlier_window(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = _entry_with_pv(hass)
    saved_at = datetime(2026, 11, 15, 12, 14, tzinfo=UTC)
    _restore(hass, entry, {"solar": "300", "load_grid": "unknown"}, saved_at)
    await _at(hass, freezer, "2026-11-15T12:21:00+00:00", grid=-500, ev=2000, pv=3000)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _at(hass, freezer, "2026-11-15T12:21:36+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 30
    assert float(value(hass, "load_grid")) == 0


async def test_an_interval_across_the_boundary_counts_its_part_in_the_new_window(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _at(hass, freezer, "2026-11-15T12:14:58+00:00", grid=-500, ev=2000, pv=3000)
    await setup_entry(hass, _sources(pv_power=PV))
    # 2 s before the boundary, 34 s after: the new window gets 34 s of 3000 W.
    await _at(hass, freezer, "2026-11-15T12:15:34+00:00", grid=-500, ev=2000, pv=3000)
    assert float(value(hass, "solar")) == 28  # 28.3 Wh
    assert float(value(hass, "load_solar")) == 19  # 18.9 Wh


async def test_a_sleeping_inverter_does_not_stop_the_loads_grid_energy(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """At night the inverter reports unavailable: the car's charging is still counted."""
    await _at(hass, freezer, "2026-11-15T03:15:01+00:00", grid=8000, ev=7000, pv="unavailable")
    await setup_entry(hass, _sources(pv_power=PV))
    # 36 s = 0.01 h: the EV drew 70 Wh, the meter counted 80 Wh (320 W over 900 s).
    await _at(
        hass,
        freezer,
        "2026-11-15T03:15:37+00:00",
        grid=8000,
        ev=7000,
        pv="unavailable",
        average=320,
    )
    assert float(value(hass, "load_grid")) == 70
    assert float(value(hass, "house_grid")) == 10
    assert float(value(hass, "solar")) == 0


async def test_a_stale_pv_power_counts_as_zero(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-15T03:13:00+00:00")
    hass.states.async_set(PV, "1500", {"unit_of_measurement": "W"})  # two minutes old soon
    await _at(hass, freezer, "2026-11-15T03:15:01+00:00", grid=8000, ev=7000)
    await setup_entry(hass, _sources(pv_power=PV))
    await _at(hass, freezer, "2026-11-15T03:15:37+00:00", grid=8000, ev=7000, average=320)
    assert float(value(hass, "load_grid")) == 70
    assert float(value(hass, "solar")) == 0


async def test_implausible_export_counter_falls_back_to_grid_power(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A start value that makes the window's export more than any connection can take."""
    await _at(hass, freezer, "2026-11-15T12:15:01+00:00", grid=-500, ev=0, export_kwh=10.0)
    recorder = MagicMock()
    # A row in Wh where the register counts kWh: about the whole register in one window.
    recorder.async_add_executor_job = AsyncMock(
        return_value={EXPORT: [State(EXPORT, "10", {"unit_of_measurement": "Wh"})]}
    )
    with patch("custom_components.peak_basket.coordinator.get_instance", return_value=recorder):
        await setup_entry(hass, _sources(grid_export_energy=EXPORT))
    await _at(hass, freezer, "2026-11-15T12:15:37+00:00", grid=-500, ev=0, export_kwh=10.005)
    # -500 W for 36 s, from the grid power.
    assert float(value(hass, "export")) == 5


async def test_a_meter_ahead_keeps_the_windows_totals_up_to_the_boundary(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """The meter's clock runs a few seconds ahead: its average resets before the boundary.

    The window's import only grows, so a drop in its last seconds is the meter starting
    the next window. Until Home Assistant's boundary the window keeps its last value.
    """
    await _at(hass, freezer, "2026-11-15T07:59:40+00:00", grid=1900, ev=0)
    await setup_entry(hass, _sources())
    await _at(hass, freezer, "2026-11-15T07:59:52+00:00", grid=1900, ev=0, average=1900)
    assert float(value(hass, "used")) == 475  # 1900 W over 900 s
    assert float(value(hass, "house_grid")) == 475

    # Between reports the grid power adds the import since: 1900 W for 4 s is 2.1 Wh.
    freezer.move_to("2026-11-15T07:59:56+00:00")
    await tick(hass)
    assert float(value(hass, "used")) == 477  # 475 + 2.1

    caplog.set_level(logging.DEBUG, "custom_components.peak_basket")
    await _at(hass, freezer, "2026-11-15T07:59:58+00:00", grid=1900, ev=0, average=2)
    # Kept, with the import since the last report before the drop: it never goes down.
    assert float(value(hass, "used")) == 478  # 475 + 3.2
    # The meter's clock runs at least the seconds left ahead.
    assert "Window average dropped 2 s before the boundary" in caplog.text
    assert float(value(hass, "house_grid")) == 478  # 475 + 3.2

    await _at(hass, freezer, "2026-11-15T08:00:03+00:00", grid=1900, ev=0, average=40)
    assert float(value(hass, "used")) == 10


async def test_a_drop_well_before_the_boundary_is_not_a_rollover(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _at(hass, freezer, "2026-11-15T07:56:00+00:00", grid=1900, ev=0)
    await setup_entry(hass, _sources())
    await _at(hass, freezer, "2026-11-15T07:56:30+00:00", grid=1900, ev=0, average=1900)
    assert float(value(hass, "used")) == 475
    await _at(hass, freezer, "2026-11-15T07:57:00+00:00", grid=1900, ev=0, average=40)
    assert float(value(hass, "used")) == 10
