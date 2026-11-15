"""Sensors: the basket fields and the pull output, the allowed power of the load."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PeakBasketConfigEntry, PeakBasketCoordinator, floor_power
from .model import Mode

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

# Sensors only compute; the coordinator serialises the work.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PeakBasketSensorDescription(SensorEntityDescription):
    """A sensor and the coordinator value it shows."""

    value_fn: Callable[[PeakBasketCoordinator], float | str | None]


def _wh(value: float | None) -> float | None:
    return None if value is None else round(value)


def _w(value: float | None) -> float | None:
    return None if value is None else floor_power(value)


def _power(
    key: str, value_fn: Callable[[PeakBasketCoordinator], float | None]
) -> PeakBasketSensorDescription:
    return PeakBasketSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: _w(value_fn(c)),
    )


def _budget(
    key: str,
    value_fn: Callable[[PeakBasketCoordinator], float | None],
    entity_category: EntityCategory | None = None,
) -> PeakBasketSensorDescription:
    return PeakBasketSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=entity_category,
        value_fn=lambda c: _wh(value_fn(c)),
    )


SENSORS: tuple[PeakBasketSensorDescription, ...] = (
    _power("allowed_load_power", lambda c: c.data.allowed_load_w),
    _power("grid_limit", lambda c: c.data.limit_w),
    PeakBasketSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=[m.value for m in Mode],
        value_fn=lambda c: c.data.mode.value,
    ),
    PeakBasketSensorDescription(
        key="charge_minutes",
        translation_key="charge_minutes",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.data.charge_minutes),
    ),
    _power("target", lambda c: c.target_w),
    _power("house_power", lambda c: c.data.house_w),
    _budget("basket", lambda c: c.data.basket_wh),
    _budget("used", lambda c: c.data.used_wh),
    _budget("remaining", lambda c: c.data.remaining_wh),
    _budget("house_reserve", lambda c: c.data.house_reserve_wh, EntityCategory.DIAGNOSTIC),
    _budget("load_room", lambda c: c.data.load_room_wh),
    _budget("margin", lambda c: c.data.margin_wh, EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: PeakBasketConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of an entry."""
    async_add_entities(PeakBasketSensor(entry.runtime_data, d) for d in SENSORS)


class PeakBasketSensor(CoordinatorEntity[PeakBasketCoordinator], SensorEntity):
    """One value of the model."""

    _attr_has_entity_name = True
    entity_description: PeakBasketSensorDescription

    def __init__(
        self, coordinator: PeakBasketCoordinator, description: PeakBasketSensorDescription
    ) -> None:
        """Attach the sensor to the entry's service device."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> float | str | None:
        """Return the value from the latest evaluation."""
        return self.entity_description.value_fn(self.coordinator)
