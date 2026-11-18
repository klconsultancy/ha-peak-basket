"""Sensors: the grid connection's basket, and per load the power it may draw."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

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
    house: bool = False
    """Shown on the house's device rather than the connection's."""


@dataclass(frozen=True, kw_only=True)
class LoadSensorDescription(SensorEntityDescription):
    """A sensor of a load and the value of the load's evaluation it shows."""

    value_fn: Callable[[PeakBasketData, str], float | str | None]
    """From the latest evaluation and the load's subentry id."""
    restore: bool = False
    """Hand the last value back to the coordinator after a restart, within its window."""


# The entity ID's suffix per key: the slug of the sensor's English name, the same in every
# language (tests/test_names.py holds it to strings.json).
SUFFIXES = {
    "basket": "window_budget",
    "used": "grid_import",
    "remaining": "budget_left",
    "margin": "reserve",
    "target": "peak_limit",
    "solar": "solar_production",
    "export": "export",
    "house_power": "power",
    "house_grid": "grid_import",
    "house_solar": "solar_import",
    "house_reserve": "expected_import",
    "power": "power",
    "allowed_power": "allowed_power",
    "grid_limit": "grid_limit",
    "mode": "mode",
    "charge_minutes": "run_minutes_left",
    "load_room": "budget_available",
    "load_reserve": "expected_import",
    "load_grid": "grid_import",
    "load_solar": "solar_import",
}


def load_slug(title: str) -> str:
    """Return the load's part of its entity IDs: never one the house's could have."""
    slug = slugify(title)
    return f"{slug}_load" if slug == "house" or slug.startswith("house_") else slug


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
        house=True,
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
            house=key == "house_reserve",
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


# Every load's power as the model uses it: missing, stale or negative is 0 W.
LOAD_POWER = LoadSensorDescription(
    key="power",
    translation_key="power",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d, i: _w(d.powers.get(i)),
)

# A measured-only load's expected import: its only basket figure, so not diagnostic.
LOAD_RESERVE = LoadSensorDescription(
    key="load_reserve",
    translation_key="load_reserve",
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    value_fn=lambda d, i: _wh(d.reserves.get(i)),
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
HOUSE_GRID = replace(
    _energy_sensor(
        ConnectionSensorDescription, "house_grid", lambda c: _wh(c.data.flows.house_grid)
    ),
    house=True,
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
    replace(
        _energy_sensor(
            ConnectionSensorDescription, "house_solar", lambda c: _wh(c.data.flows.house_solar)
        ),
        house=True,
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
    # Who drew from the basket, for every load; the decision only for a steered one, the
    # reserve only for a measured-only one.
    imports = [LOAD_GRID, LOAD_SOLAR] if solar else [LOAD_GRID]
    if solar:
        connection_sensors += SOLAR
    if solar or register:
        connection_sensors.append(_export(from_register=register))
    # A source removed through Reconfigure takes its sensors with it, rather than leaving
    # them behind as unavailable.
    load_sensors = {
        load.subentry_id: [LOAD_POWER, *LOAD_SENSORS, *imports]
        if load.steered
        else [LOAD_POWER, LOAD_RESERVE, *imports]
        for load in coordinator.loads
    }
    expected = {f"{entry.entry_id}_{d.key}" for d in connection_sensors} | {
        f"{entry.entry_id}_{subentry_id}_{d.key}"
        for subentry_id, descriptions in load_sensors.items()
        for d in descriptions
    }
    registry = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registered.domain == "sensor" and registered.unique_id not in expected:
            registry.async_remove(registered.entity_id)
    async_add_entities(ConnectionSensor(coordinator, d, connection.id) for d in connection_sensors)
    for load in coordinator.loads:
        async_add_entities(
            (
                LoadSensor(coordinator, load, d, connection.id)
                for d in load_sensors[load.subentry_id]
            ),
            config_subentry_id=load.subentry_id,
        )


def _connection_device(coordinator: PeakBasketCoordinator) -> DeviceInfo:
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        translation_key="connection",
        entry_type=DeviceEntryType.SERVICE,
    )


def _house_device(coordinator: PeakBasketCoordinator, connection_device_id: str) -> DeviceInfo:
    """Describe the house: what the connection uses that Peak Basket does not steer."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_house")},
        translation_key="house",
        entry_type=DeviceEntryType.SERVICE,
        via_device_id=connection_device_id,
    )


class ConnectionSensor(CoordinatorEntity[PeakBasketCoordinator], RestoreEntity, SensorEntity):
    """One value of the grid connection's basket, or of the house."""

    _attr_has_entity_name = True
    entity_description: ConnectionSensorDescription

    def __init__(
        self,
        coordinator: PeakBasketCoordinator,
        description: ConnectionSensorDescription,
        connection_device_id: str,
    ) -> None:
        """Attach the sensor to the connection's device, or the house's under it."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = (
            _house_device(coordinator, connection_device_id)
            if description.house
            else _connection_device(coordinator)
        )
        # Names are translated: keep new entity IDs the same in every language.
        device = "house_" if description.house else ""
        self.entity_id = f"sensor.{DOMAIN}_{device}{SUFFIXES[description.key]}"

    @property
    def available(self) -> bool:
        """Unavailable until the coordinator published its first evaluation."""
        return super().available and self.coordinator.data is not None

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
        # Only used when the entity is first registered: it keeps its ID after a rename.
        self.entity_id = f"sensor.{DOMAIN}_{load_slug(load.title)}_{SUFFIXES[description.key]}"

    @property
    def available(self) -> bool:
        """Unavailable until the coordinator published its first evaluation."""
        return super().available and self.coordinator.data is not None

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
