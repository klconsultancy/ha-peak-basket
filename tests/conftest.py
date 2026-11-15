"""Fixtures for the Peak Basket tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.peak_basket.const import DOMAIN, TICK_SECONDS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

GRID = "sensor.p1_power"
IMPORT = "sensor.p1_import"
AVERAGE = "sensor.p1_average"
EV = "sensor.ev_power"
# 300 s into the 03:00 window.
START = datetime(2026, 11, 15, 3, 5, tzinfo=UTC)

SOURCES: dict[str, Any] = {
    "name": "Peak Basket",
    "profile": "be_flanders",
    "grid_power": GRID,
    "grid_import_energy": IMPORT,
    "load_power": EV,
}
# What the config flow asks in its second step, for the first load.
LOAD_FIELDS = {
    "load_power",
    "voltages",
    "priority",
    "min_a",
    "max_a",
    "reaction_s",
    "min_charge_s",
    "extra_a",
}
LOAD_KEYS = {
    "allowed_power",
    "grid_limit",
    "mode",
    "charge_minutes",
    "load_room",
    "load_solar",
    "load_grid",
}
LOAD_NAME = "EV charger"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/ in every test."""


def set_sources(
    hass: HomeAssistant, grid_w: float, import_kwh: float | str, ev_w: float | str
) -> None:
    """Report the P1 power, the import register and the load power."""
    hass.states.async_set(GRID, str(grid_w), {"unit_of_measurement": "W"})
    hass.states.async_set(IMPORT, str(import_kwh), {"unit_of_measurement": "kWh"})
    hass.states.async_set(EV, str(ev_w), {"unit_of_measurement": "W"})


def value(hass: HomeAssistant, key: str) -> str:
    """Return the state of a sensor of the entry or its first load, found by its unique id."""
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    unique_id = f"{entry.entry_id}_{key}"
    if key in LOAD_KEYS:
        subentry = next(iter(entry.subentries.values()))
        unique_id = f"{entry.entry_id}_{subentry.subentry_id}_{key}"
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def tick(hass: HomeAssistant) -> None:
    """Let the coordinator's safety tick run.

    The event loop's clock does not freeze with the wall clock, so ask for the timers due
    within one tick from now.
    """
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=TICK_SECONDS + 1))
    await hass.async_block_till_done()


async def setup_entry(hass: HomeAssistant, sources: dict[str, Any] | None = None) -> ConfigEntry:
    """Add an entry and its first load through the config flow, and wait for its sensors.

    ``sources`` holds the fields of both steps: the load's go to the second.
    """
    fields = SOURCES if sources is None else sources
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {k: v for k, v in fields.items() if k not in LOAD_FIELDS}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "load"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": LOAD_NAME} | {k: v for k, v in fields.items() if k in LOAD_FIELDS},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry: ConfigEntry = result["result"]
    return entry
