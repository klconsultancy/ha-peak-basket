"""Sensors: the grid connection's basket, and per load the power it may draw."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_GRID_EXPORT_ENERGY,
    CONF_PV_POWER,
    DOMAIN,
    REPORT_SECONDS,
    TICK_SECONDS,
)
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
    report: bool = False
    """Written at most every REPORT_SECONDS and at the window's edges; see _Sensor."""
    house: bool = False
    """Shown on the house's device rather than the connection's."""


@dataclass(frozen=True, kw_only=True)
class LoadSensorDescription(SensorEntityDescription):
    """A sensor of a load and the value of the load's evaluation it shows."""

    value_fn: Callable[[PeakBasketData, str], float | str | None]
    """From the latest evaluation and the load's subentry id."""
    restore: bool = False
    """Hand the last value back to the coordinator after a restart, within its window."""
    report: bool = False
    """Written at most every REPORT_SECONDS and at the window's edges; see _Sensor."""


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
            report=True,
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
        report=True,
    ),
    LoadSensorDescription(
        key="load_room",
        translation_key="load_room",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d, i: _wh(d.loads[i].load_room_wh),
        report=True,
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
    report=True,
)

# A measured-only load's expected import: its only basket figure, so not diagnostic.
LOAD_RESERVE = LoadSensorDescription(
    key="load_reserve",
    translation_key="load_reserve",
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    value_fn=lambda d, i: _wh(d.reserves.get(i)),
    report=True,
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
        report=True,
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
        device = _load_device(hass, coordinator, load, connection.id)
        async_add_entities(
            (LoadSensor(coordinator, load, d, device) for d in load_sensors[load.subentry_id]),
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


def _load_device(
    hass: HomeAssistant, coordinator: PeakBasketCoordinator, load: Load, connection_device_id: str
) -> DeviceInfo:
    """Describe a load's device, linked to the appliance its power sensor belongs to.

    The device is Peak Basket's own, so it only points to the appliance's: not
    ``via_device``, as Peak Basket does not talk to the appliance. When it links to
    another appliance than before (also when it is created), it takes the appliance's area,
    or the power sensor's, unless it has an area already. A reload changes nothing, so an
    area the user picked or cleared is kept.
    """
    entry_id = coordinator.config_entry.entry_id
    identifier = (DOMAIN, f"{entry_id}_{load.subentry_id}")
    power = er.async_get(hass).async_get(load.power_entity)
    devices = dr.async_get(hass)
    appliance = devices.async_get(power.device_id) if power and power.device_id else None
    url = f"homeassistant://config/devices/device/{appliance.id}" if appliance else None
    before = devices.async_get_device_by_identifier(identifier, entry_id)
    info = DeviceInfo(
        identifiers={identifier},
        name=load.title,
        entry_type=DeviceEntryType.SERVICE,
        via_device_id=connection_device_id,
        configuration_url=url,
    )
    device = devices.async_get_or_create(
        config_entry_id=entry_id, config_subentry_id=load.subentry_id, **info
    )
    # The link records the appliance: the area comes along only when it changes.
    linked = before is None or before.configuration_url != url
    area = (appliance.area_id if appliance else None) or (power.area_id if power else None)
    if linked and device.area_id is None and area is not None:
        devices.async_update_device(device.id, area_id=area)
    return info


@dataclass
class WindowSum(ExtraStoredData):
    """A sum and the start of the window it belongs to, kept for a restart."""

    wh: float
    window: datetime

    def as_dict(self) -> dict[str, Any]:
        """Return the sum and its window as JSON."""
        return {"wh": self.wh, "window": self.window.isoformat()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WindowSum | None:
        """Return the saved sum, or None when it is not one."""
        try:
            return cls(float(data["wh"]), datetime.fromisoformat(data["window"]))
        except KeyError, TypeError, ValueError:
            return None


class _Sensor(CoordinatorEntity[PeakBasketCoordinator], RestoreEntity, SensorEntity):
    """What a connection's and a load's sensor share: when to write, and the restore.

    A control sensor writes on every published evaluation. A report sensor writes only
    when its history needs it, since every written change is a recorder row (#95): after
    REPORT_SECONDS, in the window's last tick (so the last row before the reset is the
    window's total), on the first evaluation of a new window, and when it goes unknown or
    comes back. Its native_value is always current; only what is written is recorded.
    """

    _attr_has_entity_name = True
    entity_description: ConnectionSensorDescription | LoadSensorDescription
    _subentry_id: str | None = None
    # The last write: when, the window of its value, and whether it had one. Set when the
    # sensor is added; coordinator updates only come after that.
    _written: tuple[datetime, datetime | None, bool]

    @property
    def available(self) -> bool:
        """Unavailable until the coordinator published its first evaluation."""
        return super().available and self.coordinator.data is not None

    def _window(self) -> datetime | None:
        return self.coordinator.data.window if self.coordinator.data is not None else None

    def _known(self) -> bool:
        return self.available and self.native_value is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.entity_description.report and not self._report_due():
            return
        super()._handle_coordinator_update()
        self._written = (dt_util.now(), self._window(), self._known())

    def _report_due(self) -> bool:
        at, window, known = self._written
        now = dt_util.now()
        current = self._window()
        end = (current or now) + timedelta(seconds=self.coordinator.profile.window_s)
        return (
            current != window
            or self._known() != known
            or (end - now).total_seconds() <= TICK_SECONDS
            or now - at >= timedelta(seconds=REPORT_SECONDS)
        )

    @property
    def extra_restore_state_data(self) -> WindowSum | None:
        """Return the sum as it is now and its window, when the restore state is saved.

        Taken at the moment Home Assistant saves (every 15 min and on stop), not from the
        last written state, which for a report sensor can be 30 s old.
        """
        value, window = self.native_value, self._window()
        if (
            not self.entity_description.restore
            or window is None
            or not isinstance(value, float | int)
        ):
            return None
        return WindowSum(float(value), window)

    async def async_added_to_hass(self) -> None:
        """Hand a sum from before a restart back to the coordinator.

        Home Assistant writes the state right after this: that is the first write.
        """
        await super().async_added_to_hass()
        if self.entity_description.restore:
            await _restore(self, self._subentry_id)
        self._written = (dt_util.now(), self._window(), self._known())


class ConnectionSensor(_Sensor):
    """One value of the grid connection's basket, or of the house."""

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
    def native_value(self) -> float | None:
        """Return the value from the latest evaluation."""
        return self.entity_description.value_fn(self.coordinator)


class LoadSensor(_Sensor):
    """One value of a load's decision."""

    entity_description: LoadSensorDescription
    _subentry_id: str

    def __init__(
        self,
        coordinator: PeakBasketCoordinator,
        load: Load,
        description: LoadSensorDescription,
        device: DeviceInfo,
    ) -> None:
        """Attach the sensor to the load's device, under the connection's."""
        super().__init__(coordinator)
        self.entity_description = description
        self._subentry_id = load.subentry_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{load.subentry_id}_{description.key}"
        self._attr_device_info = device
        # Only used when the entity is first registered: it keeps its ID after a rename.
        self.entity_id = f"sensor.{DOMAIN}_{load_slug(load.title)}_{SUFFIXES[description.key]}"

    @property
    def native_value(self) -> float | str | None:
        """Return the value from the latest evaluation."""
        return self.entity_description.value_fn(self.coordinator.data, self._subentry_id)


async def _restore(sensor: _Sensor, subentry_id: str | None) -> None:
    key = sensor.entity_description.key
    extra = await sensor.async_get_last_extra_data()
    saved = WindowSum.from_dict(extra.as_dict()) if extra is not None else None
    if saved is not None:
        sensor.coordinator.restore(key, subentry_id, saved.wh, saved.window)
        return
    # Saved before 0.2.1: the written state, at the moment it was written.
    last = await sensor.async_get_last_state()
    if last is None:
        return
    with suppress(ValueError):
        sensor.coordinator.restore(key, subentry_id, float(last.state), last.last_updated)
