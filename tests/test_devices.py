"""The connection's device and the entity IDs of its sensors."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.peak_basket.const import DOMAIN

from .conftest import set_sources, setup_entry

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
