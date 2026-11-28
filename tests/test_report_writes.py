"""Report sensors write at most every 30 s and at the window's edges (#95).

Every written change is a recorder row. The control outputs (allowed power, grid limit,
mode, peak limit, the house's power) write on every published evaluation, as before; the
report sensors (the window's energy sums, a load's power, the minutes left) only when a
reader of their history needs it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from custom_components.peak_basket.const import DOMAIN
from custom_components.peak_basket.sensor import _Sensor

from .conftest import AVERAGE, EV, GRID, LOAD_KEYS, SOURCES, setup_entry, tick

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant


@pytest.fixture
def report_on_every_publish() -> None:
    """Keep the report sensors at REPORT_SECONDS: what these tests are about."""


def _entity_id(hass: HomeAssistant, key: str) -> str:
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    unique_id = f"{entry.entry_id}_{key}"
    if key in LOAD_KEYS:
        subentry = next(iter(entry.subentries.values()))
        unique_id = f"{entry.entry_id}_{subentry.subentry_id}_{key}"
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


def _record(hass: HomeAssistant, keys: list[str]) -> dict[str, list[tuple[datetime, str]]]:
    """Collect each sensor's written changes: what the recorder would store."""
    rows: dict[str, list[tuple[datetime, str]]] = {key: [] for key in keys}
    by_id = {_entity_id(hass, key): key for key in keys}

    @callback
    def changed(event: Event[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        assert state is not None
        rows[by_id[event.data["entity_id"]]].append((state.last_updated, state.state))

    async_track_state_change_event(hass, list(by_id), changed)
    return rows


def _sources() -> dict[str, Any]:
    """The meter's window average: the used energy is known from the first evaluation."""
    sources: dict[str, Any] = {**SOURCES, "window_average": AVERAGE}
    del sources["grid_import_energy"]
    return sources


async def _setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: datetime) -> None:
    freezer.move_to(at)
    hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
    hass.states.async_set(EV, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(AVERAGE, str(_average(at)), {"unit_of_measurement": "W"})
    await setup_entry(hass, _sources())


def _average(at: datetime) -> float:
    """The meter's average so far at 3600 W since the window start: 1 Wh a second."""
    return 3600 * (at.minute % 15 * 60 + at.second) / 900


async def _step(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: datetime, grid: float
) -> None:
    """Move to ``at``, report the grid power and the window average, and evaluate."""
    freezer.move_to(at)
    hass.states.async_set(GRID, str(grid), {"unit_of_measurement": "W"})
    hass.states.async_set(AVERAGE, str(_average(at)), {"unit_of_measurement": "W"})
    await tick(hass)


async def test_report_sensors_write_every_30_s_control_sensors_on_every_change(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A minute of ticks with a changing house, in the middle of a window."""
    start = datetime(2026, 11, 15, 3, 5, tzinfo=UTC)
    await _setup(hass, freezer, start)
    report = ["used", "remaining", "charge_minutes", "load_room"]
    control = ["grid_limit", "house_power"]
    rows = _record(hass, report + control)
    for k in range(1, 13):
        await _step(hass, freezer, start + timedelta(seconds=5 * k), 2000 + 100 * k)
    for key in control:
        assert len(rows[key]) == 12, key
    for key in report:
        assert [at for at, _ in rows[key]] == [
            start + timedelta(seconds=30),
            start + timedelta(seconds=60),
        ], key


async def test_the_window_ends_on_its_total_and_starts_from_zero(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The last row before the boundary is the window's total, the first after it ~0."""
    start = datetime(2026, 11, 15, 3, 14, 30, tzinfo=UTC)
    boundary = datetime(2026, 11, 15, 3, 15, tzinfo=UTC)
    await _setup(hass, freezer, start)
    rows = _record(hass, ["used"])
    for k in range(1, 9):
        await _step(hass, freezer, start + timedelta(seconds=5 * k), 3600)
    # 03:14:55, the last tick of the window (5 s left), and 03:15:00, the first of the next.
    assert rows["used"] == [(boundary - timedelta(seconds=5), "895"), (boundary, "0")]


async def test_a_report_sensor_writes_at_once_when_its_value_goes_unknown_and_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    start = datetime(2026, 11, 15, 3, 5, tzinfo=UTC)
    await _setup(hass, freezer, start)
    rows = _record(hass, ["used"])
    freezer.move_to(start + timedelta(seconds=5))
    hass.states.async_set(AVERAGE, "unavailable")
    await tick(hass)
    await _step(hass, freezer, start + timedelta(seconds=10), 2000)
    assert [(at - start).total_seconds() for at, state in rows["used"]] == [5, 10]
    assert rows["used"][0][1] == "unknown"


async def test_an_update_while_a_sensor_restores_is_handled(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """The coordinator listener is registered before the restore awaits Home Assistant.

    An evaluation published in between reaches a report sensor that has not been written
    yet: it must find when it was added. The coordinator would only log the failure, and
    the update would be lost.
    """

    async def publish_meanwhile(sensor: _Sensor) -> None:
        await asyncio.sleep(0)
        sensor.coordinator.async_update_listeners()

    with patch.object(_Sensor, "async_get_last_extra_data", publish_meanwhile):
        await _setup(hass, freezer, datetime(2026, 11, 15, 3, 5, tzinfo=UTC))
    assert hass.states.get(_entity_id(hass, "load_grid")) is not None
    assert "Unexpected error updating listener" not in caplog.text
