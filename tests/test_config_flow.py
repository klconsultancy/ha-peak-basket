from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.peak_basket.const import DEFAULT_OPTIONS, DOMAIN

from .conftest import EV, GRID, SOURCES, set_sources, setup_entry, value

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant


async def test_creates_entry_with_default_options(hass: HomeAssistant) -> None:
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    assert entry.title == "Peak Basket"
    assert "name" not in entry.data
    assert entry.data["grid_power"] == GRID
    assert dict(entry.options) == DEFAULT_OPTIONS


async def test_rejects_the_grid_sensor_as_load(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SOURCES, "load_power": GRID}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"load_power": "same_as_grid"}


async def test_requires_an_energy_source(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "x", "profile": "be_flanders", "grid_power": GRID, "load_power": EV},
    )
    assert result["errors"] == {"base": "no_energy_source"}


async def test_options_change_the_model_and_reload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-15T03:05:00+00:00")
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    assert float(value(hass, "grid_limit")) == 5400  # fallback: target 5500 - margin 100

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DEFAULT_OPTIONS, "peak_goal_w": 6000.0, "margin_w": 200.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert float(value(hass, "target")) == 6000
    assert float(value(hass, "grid_limit")) == 5800
