"""Peak Basket: budget the grid import per capacity-tariff window."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .coordinator import PeakBasketConfigEntry, PeakBasketCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PLATFORMS = [Platform.SENSOR]


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


async def _async_reload(hass: HomeAssistant, entry: PeakBasketConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
