from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.peak_basket.const import DEFAULT_ENTRY_OPTIONS, DEFAULT_LOAD, DOMAIN

from .conftest import EV, GRID, IMPORT, LOAD_NAME, SOURCES, set_sources, setup_entry, value

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

CONNECTION: dict[str, Any] = {
    "name": "Peak Basket",
    "profile": "be_flanders",
    "grid_power": GRID,
    "grid_import_energy": IMPORT,
}


async def test_creates_the_connection_with_its_first_load(hass: HomeAssistant) -> None:
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    assert entry.title == "Peak Basket"
    assert entry.version == 2
    assert dict(entry.data) == {
        "profile": "be_flanders",
        "grid_power": GRID,
        "grid_import_energy": IMPORT,
    }
    assert dict(entry.options) == DEFAULT_ENTRY_OPTIONS
    (load,) = entry.subentries.values()
    assert load.subentry_type == "load"
    assert load.title == LOAD_NAME
    assert dict(load.data) == {"load_power": EV, **DEFAULT_LOAD}


async def test_requires_an_energy_source(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "x", "profile": "be_flanders", "grid_power": GRID}
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_energy_source"}


async def test_rejects_the_grid_sensor_as_load(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CONNECTION)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": LOAD_NAME, "load_power": GRID}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "load"
    assert result["errors"] == {"load_power": "same_as_grid"}


async def test_options_change_the_connection_and_reload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-15T03:05:00+00:00")
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    assert float(value(hass, "grid_limit")) == 5400  # fallback: target 5500 - margin 100

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DEFAULT_ENTRY_OPTIONS, "peak_goal_w": 6000.0, "margin_w": 200.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert float(value(hass, "target")) == 6000
    assert float(value(hass, "grid_limit")) == 5800


async def test_add_a_load(hass: HomeAssistant) -> None:
    set_sources(hass, 1000, 100.0, 0)
    hass.states.async_set("sensor.boiler_power", "0", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Boiler", "load_power": "sensor.boiler_power", "priority": 2, "max_a": 10.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    boiler = next(s for s in entry.subentries.values() if s.title == "Boiler")
    assert boiler.data["priority"] == 2
    assert boiler.data["max_a"] == 10.0
    assert boiler.data["min_a"] == DEFAULT_LOAD["min_a"]
    assert boiler.subentry_id in entry.runtime_data.data.loads


async def test_add_a_load_rejects_the_grid_sensor(hass: HomeAssistant) -> None:
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Oops", "load_power": GRID}
    )
    assert result["errors"] == {"load_power": "same_as_grid"}


async def test_reconfigure_a_load(hass: HomeAssistant) -> None:
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass, {**SOURCES})
    (load,) = entry.subentries.values()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": load.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Car", "load_power": GRID}
    )
    assert result["errors"] == {"load_power": "same_as_grid"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Car", "load_power": EV, "reaction_s": 30.0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    load = entry.subentries[load.subentry_id]
    assert load.title == "Car"
    assert load.data["reaction_s"] == 30.0
