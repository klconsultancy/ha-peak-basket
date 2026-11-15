"""Sensors: the grid connection's basket, and per load the power it may draw."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GRID_EXPORT_ENERGY, CONF_PV_POWER, DOMAIN
from .coordinator import PeakBasketConfigEntry, PeakBasketCoordinator, floor_power
from .model import Mode

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import Load, PeakBasketData

# Sensors only compute; the coordinator serialises the work.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ConnectionSensorDescription(SensorEntityDescription):
    """A sensor of the grid connection and the coordinator value it shows."""

    value_fn: Callable[[PeakBasketCoordinator], float | None]
    restore: bool = False
    """Hand the last value back to the coordinator after a restart, within its window."""


@dataclass(frozen=True, kw_only=True)
class LoadSensorDescription(SensorEntityDescription):
    """A sensor of a load and the value of the load's evaluation it shows."""

    value_fn: Callable[[PeakBasketData, str], float | str | None]
    """From the latest evaluation and the load's subentry id."""
    restore: bool = False
    """Hand the last value back to the coordinator after a restart, within its window."""


def _wh(value: float | None) -> float | None:
    return None if value is None else round(value)


def _w(value: float | None) -> float | None:
    return None if value is None else floor_power(value)


CONNECTION_SENSORS: tuple[ConnectionSensorDescription, ...] = (
    ConnectionSensorDescription(
        key="target",
        translation_key="target",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: _w(c.target_w),
    ),
    ConnectionSensorDescription(
        key="house_power",
        translation_key="house_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: _w(c.data.connection.house_w),
    ),
    *(
        ConnectionSensorDescription(
            key=key,
            translation_key=key,
            native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
            entity_category=category,
            value_fn=fn,
        )
        for key, fn, category in (
            ("basket", lambda c: _wh(c.data.connection.basket_wh), None),
            ("used", lambda c: _wh(c.data.connection.used_wh), None),
            ("remaining", lambda c: _wh(c.data.connection.remaining_wh), None),
            (
                "house_reserve",
                lambda c: _wh(c.data.connection.house_reserve_wh),
                EntityCategory.DIAGNOSTIC,
            ),
            ("margin", lambda c: _wh(c.data.connection.margin_wh), EntityCategory.DIAGNOSTIC),
        )
    ),
)

LOAD_SENSORS: tuple[LoadSensorDescription, ...] = (
    LoadSensorDescription(
        key="allowed_power",
        translation_key="allowed_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d, i: _w(d.loads[i].allowed_load_w),
    ),
    LoadSensorDescription(
        key="grid_limit",
        translation_key="grid_limit",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d, i: _w(d.loads[i].limit_w),
    ),
    LoadSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=[m.value for m in Mode],
        value_fn=lambda d, i: d.loads[i].mode.value,
    ),
    LoadSensorDescription(
        key="charge_minutes",
        translation_key="charge_minutes",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d, i: round(d.loads[i].charge_minutes),
    ),
    LoadSensorDescription(
        key="load_room",
        translation_key="load_room",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d, i: _wh(d.loads[i].load_room_wh),
    ),
)


def _energy_sensor[D: (ConnectionSensorDescription, LoadSensorDescription)](
    cls: type[D], key: str, value_fn: Callable[..., float | None], *, restore: bool = False
) -> D:
    return cls(
        key=key,
        translation_key=key,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=value_fn,
        restore=restore,
    )


# Who drew from the basket this window. Sums the coordinator adds up itself are restored
# after a restart; the rest it derives.
HOUSE_GRID = _energy_sensor(
    ConnectionSensorDescription, "house_grid", lambda c: _wh(c.data.flows.house_grid)
)
LOAD_GRID = _energy_sensor(
    LoadSensorDescription,
    "load_grid",
    lambda d, i: _wh(d.flows.load_grid.get(i, 0.0)),
    restore=True,
)
SOLAR = (
    _energy_sensor(
        ConnectionSensorDescription, "solar", lambda c: _wh(c.data.flows.solar), restore=True
    ),
    _energy_sensor(
        ConnectionSensorDescription, "house_solar", lambda c: _wh(c.data.flows.house_solar)
    ),
)
LOAD_SOLAR = _energy_sensor(
    LoadSensorDescription,
    "load_solar",
    lambda d, i: _wh(d.flows.load_solar.get(i, 0.0)),
    restore=True,
)


def _export(*, from_register: bool) -> ConnectionSensorDescription:
    """Export from the register is read again after a restart; from grid power, restored."""
    return _energy_sensor(
        ConnectionSensorDescription,
        "export",
        lambda c: _wh(c.data.flows.export),
        restore=not from_register,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PeakBasketConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the connection's sensors, and each load's on the load's own subentry."""
    coordinator = entry.runtime_data
    # Created here, so each load's device can name it as the device it is reached through.
    connection = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **_connection_device(coordinator)
    )
    solar = CONF_PV_POWER in entry.data
    register = CONF_GRID_EXPORT_ENERGY in entry.data
    connection_sensors = [*CONNECTION_SENSORS, HOUSE_GRID]
    load_sensors = [*LOAD_SENSORS, LOAD_GRID]
    if solar:
        connection_sensors += SOLAR
        load_sensors.append(LOAD_SOLAR)
    if solar or register:
        connection_sensors.append(_export(from_register=register))
    async_add_entities(ConnectionSensor(coordinator, d) for d in connection_sensors)
    for load in coordinator.loads:
        async_add_entities(
            (LoadSensor(coordinator, load, d, connection.id) for d in load_sensors),
            config_subentry_id=load.subentry_id,
        )


def _connection_device(coordinator: PeakBasketCoordinator) -> DeviceInfo:
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        entry_type=DeviceEntryType.SERVICE,
    )


class ConnectionSensor(CoordinatorEntity[PeakBasketCoordinator], RestoreEntity, SensorEntity):
    """One value of the grid connection's basket."""

    _attr_has_entity_name = True
    entity_description: ConnectionSensorDescription

    def __init__(
        self, coordinator: PeakBasketCoordinator, description: ConnectionSensorDescription
    ) -> None:
        """Attach the sensor to the connection's service device."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _connection_device(coordinator)

    @property
    def native_value(self) -> float | None:
        """Return the value from the latest evaluation."""
        return self.entity_description.value_fn(self.coordinator)

    async def async_added_to_hass(self) -> None:
        """Hand a sum from before a restart back to the coordinator."""
        await super().async_added_to_hass()
        if self.entity_description.restore:
            await _restore(self, None)


class LoadSensor(CoordinatorEntity[PeakBasketCoordinator], RestoreEntity, SensorEntity):
    """One value of a load's decision."""

    _attr_has_entity_name = True
    entity_description: LoadSensorDescription

    def __init__(
        self,
        coordinator: PeakBasketCoordinator,
        load: Load,
        description: LoadSensorDescription,
        connection_device_id: str,
    ) -> None:
        """Attach the sensor to the load's device, under the connection's."""
        super().__init__(coordinator)
        self.entity_description = description
        self._subentry_id = load.subentry_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{load.subentry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{load.subentry_id}")},
            name=load.title,
            entry_type=DeviceEntryType.SERVICE,
            via_device_id=connection_device_id,
        )

    @property
    def native_value(self) -> float | str | None:
        """Return the value from the latest evaluation."""
        return self.entity_description.value_fn(self.coordinator.data, self._subentry_id)

    async def async_added_to_hass(self) -> None:
        """Hand a sum from before a restart back to the coordinator."""
        await super().async_added_to_hass()
        if self.entity_description.restore:
            await _restore(self, self._subentry_id)


async def _restore(sensor: ConnectionSensor | LoadSensor, subentry_id: str | None) -> None:
    last = await sensor.async_get_last_state()
    if last is None:
        return
    with suppress(ValueError):
        sensor.coordinator.restore(
            sensor.entity_description.key, subentry_id, float(last.state), last.last_updated
        )
