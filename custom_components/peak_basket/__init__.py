"""Peak Basket: budget the grid import per capacity-tariff window."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_EXTRA_A,
    CONF_LOAD_POWER,
    CONF_LOAD_VOLTAGES,
    CONF_MAX_A,
    CONF_METER_VOLTAGES,
    CONF_MIN_A,
    CONF_MIN_CHARGE,
    CONF_PRIORITY,
    CONF_REACTION,
    DEFAULT_LOAD,
    DOMAIN,
    LOGGER,
    SUBENTRY_LOAD,
)
from .coordinator import PeakBasketConfigEntry, PeakBasketCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PLATFORMS = [Platform.SENSOR]

# Version 1 kept one load in the entry. Its sensors move to the load, under these keys.
_V1_LOAD_SENSORS = {
    "allowed_load_power": "allowed_power",
    "grid_limit": "grid_limit",
    "mode": "mode",
    "charge_minutes": "charge_minutes",
    "load_room": "load_room",
}
_V1_LOAD_OPTIONS = (CONF_REACTION, CONF_MIN_CHARGE, CONF_EXTRA_A, CONF_MIN_A, CONF_MAX_A)


async def async_setup_entry(hass: HomeAssistant, entry: PeakBasketConfigEntry) -> bool:
    """Start the coordinator and the sensors of an entry."""
    coordinator = PeakBasketCoordinator(hass, entry)
    await coordinator.async_start()
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PeakBasketConfigEntry) -> bool:
    """Unload the sensors of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: PeakBasketConfigEntry) -> bool:
    """Move the one load of a version 1 entry into a load subentry.

    Home Assistant only calls this for an older version: 1, the only one before 2.
    """
    data = dict(entry.data)
    options = dict(entry.options)
    load = {
        CONF_LOAD_POWER: data.pop(CONF_LOAD_POWER),
        CONF_PRIORITY: DEFAULT_LOAD[CONF_PRIORITY],
        **{key: options.pop(key, DEFAULT_LOAD[key]) for key in _V1_LOAD_OPTIONS},
    }
    if voltages := data.pop(CONF_LOAD_VOLTAGES, None):
        load[CONF_LOAD_VOLTAGES] = voltages
    if meter := data.pop("voltages_fallback", None):
        data[CONF_METER_VOLTAGES] = meter
    subentry = ConfigSubentry(
        data=MappingProxyType(load), subentry_type=SUBENTRY_LOAD, title="Load", unique_id=None
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    hass.config_entries.async_update_entry(entry, data=data, options=options, version=2)

    registry = er.async_get(hass)
    for old_key, new_key in _V1_LOAD_SENSORS.items():
        old_id = f"{entry.entry_id}_{old_key}"
        if entity_id := registry.async_get_entity_id("sensor", DOMAIN, old_id):
            registry.async_update_entity(
                entity_id,
                new_unique_id=f"{entry.entry_id}_{subentry.subentry_id}_{new_key}",
                config_subentry_id=subentry.subentry_id,
            )
    LOGGER.info("Moved the load of %s into a load subentry", entry.title)
    return True


async def _async_reload(hass: HomeAssistant, entry: PeakBasketConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
