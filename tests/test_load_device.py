"""A load's device links to the appliance its power sensor measures, and takes its area."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.peak_basket.const import DOMAIN

from .conftest import EV, SOURCES, set_sources, setup_entry

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

OTHER = "sensor.other_power"


def _appliance(
    hass: HomeAssistant,
    entity_id: str,
    name: str,
    *,
    area: str | None = None,
    entity_area: str | None = None,
) -> dr.DeviceEntry:
    """Register a device of another integration, with ``entity_id`` as its power sensor."""
    source = MockConfigEntry(domain="appliance")
    source.add_to_hass(hass)
    areas = ar.async_get(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id, identifiers={("appliance", name)}, name=name
    )
    if area is not None:
        device = (
            dr.async_get(hass).async_update_device(
                device.id, area_id=areas.async_get_or_create(area).id
            )
            or device
        )
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "sensor", "appliance", name, config_entry=source, device_id=device.id
    )
    registry.async_update_entity(entity.entity_id, new_entity_id=entity_id)
    if entity_area is not None:
        # Only an entity with a name of its own can have an area of its own.
        registry.async_update_entity(
            entity_id, name=f"{name} power", area_id=areas.async_get_or_create(entity_area).id
        )
    return device


def _load_device(hass: HomeAssistant, entry: ConfigEntry) -> dr.DeviceEntry:
    subentry_id = next(iter(entry.subentries))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{subentry_id}"), entry.entry_id
    )
    assert device is not None
    return device


def _area(hass: HomeAssistant, name: str) -> str:
    area = ar.async_get(hass).async_get_area_by_name(name)
    assert area is not None
    return area.id


def _url(device: dr.DeviceEntry) -> str:
    return f"homeassistant://config/devices/device/{device.id}"


async def _change_power_sensor(hass: HomeAssistant, entry: ConfigEntry, entity_id: str) -> None:
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry, subentry, data=dict(subentry.data) | {"load_power": entity_id}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_load_device_links_to_the_appliance_and_takes_its_area(
    hass: HomeAssistant,
) -> None:
    appliance = _appliance(hass, EV, "Wallbox", area="Berging")
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    load = _load_device(hass, entry)
    assert load.configuration_url == _url(appliance)
    assert load.area_id == _area(hass, "Berging")


async def test_without_a_device_area_the_sensor_area_is_taken(hass: HomeAssistant) -> None:
    _appliance(hass, EV, "Wallbox", entity_area="Oprit")
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    assert _load_device(hass, entry).area_id == _area(hass, "Oprit")


async def test_an_area_the_user_picked_is_kept(hass: HomeAssistant) -> None:
    _appliance(hass, EV, "Wallbox", area="Berging")
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    garage = ar.async_get(hass).async_get_or_create("Garage").id
    dr.async_get(hass).async_update_device(_load_device(hass, entry).id, area_id=garage)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _load_device(hass, entry).area_id == garage
    other = _appliance(hass, OTHER, "Heat pump", area="Berging")
    await _change_power_sensor(hass, entry, OTHER)
    load = _load_device(hass, entry)
    assert load.area_id == garage
    assert load.configuration_url == _url(other)


async def test_the_link_follows_the_power_sensor(hass: HomeAssistant) -> None:
    _appliance(hass, EV, "Wallbox", area="Berging")
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    await _change_power_sensor(hass, entry, OTHER)
    load = _load_device(hass, entry)
    assert load.configuration_url is None
    assert load.area_id == _area(hass, "Berging")


async def test_a_power_sensor_without_a_device_gives_no_link_and_no_area(
    hass: HomeAssistant,
) -> None:
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    load = _load_device(hass, entry)
    assert load.configuration_url is None
    assert load.area_id is None


async def test_the_connection_and_the_house_are_unchanged(hass: HomeAssistant) -> None:
    _appliance(hass, EV, "Wallbox", area="Berging")
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass, SOURCES)
    devices = dr.async_get(hass)
    for identifier in (entry.entry_id, f"{entry.entry_id}_house"):
        device = devices.async_get_device_by_identifier((DOMAIN, identifier), entry.entry_id)
        assert device is not None
        assert device.configuration_url is None
        assert device.area_id is None
