"""The connection's device and the entity IDs of its sensors."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.peak_basket.const import DOMAIN

from .conftest import SOURCES, set_sources, setup_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.parametrize(("language", "name"), [("en", "Grid connection"), ("nl", "Aansluiting")])
async def test_connection_device_says_what_it_is(
    hass: HomeAssistant, language: str, name: str
) -> None:
    hass.config.language = language
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert device.name == name


@pytest.mark.parametrize("language", ["en", "nl"])
async def test_connection_sensors_keep_language_independent_ids(
    hass: HomeAssistant, language: str
) -> None:
    hass.config.language = language
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    for key in ("basket", "used", "house_grid", "target"):
        assert (
            registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
            == f"sensor.peak_basket_{key}"
        )


HOUSE_KEYS = ("house_power", "house_reserve", "house_grid", "house_solar")


@pytest.mark.parametrize(("language", "name"), [("en", "House"), ("nl", "Huis")])
async def test_house_is_its_own_device_under_the_connection(
    hass: HomeAssistant, language: str, name: str
) -> None:
    hass.config.language = language
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass, SOURCES | {"pv_power": "sensor.pv_power"})
    devices = dr.async_get(hass)
    connection = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    house = devices.async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_house"), entry.entry_id
    )
    assert connection is not None
    assert house is not None
    assert house.name == name
    assert house.via_device_id == connection.id
    registry = er.async_get(hass)
    for key in HOUSE_KEYS:
        entity = registry.async_get(f"sensor.peak_basket_{key}")
        assert entity is not None
        assert entity.device_id == house.id
        assert entity.unique_id == f"{entry.entry_id}_{key}"
    basket = registry.async_get("sensor.peak_basket_basket")
    assert basket is not None
    assert basket.device_id == connection.id


async def test_house_sensors_move_off_the_connection_device(hass: HomeAssistant) -> None:
    """An install from before the house device keeps its entities, now on the house."""
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    devices = dr.async_get(hass)
    connection = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert connection is not None
    registry = er.async_get(hass)
    registry.async_update_entity("sensor.peak_basket_house_grid", device_id=connection.id)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    house = devices.async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_house"), entry.entry_id
    )
    entity = registry.async_get("sensor.peak_basket_house_grid")
    assert house is not None
    assert entity is not None
    assert entity.device_id == house.id
