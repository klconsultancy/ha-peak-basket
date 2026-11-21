"""The entry's setup, the coordinator and the sensors, end to end."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from custom_components.peak_basket.const import DOMAIN

from .conftest import (
    AVERAGE,
    EV,
    GRID,
    IMPORT,
    SOURCES,
    START,
    set_sources,
    setup_entry,
    tick,
    value,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant


async def test_mid_window_start_without_history_falls_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(START)
    set_sources(hass, 1650, 12345.0, 0)
    await setup_entry(hass)
    assert value(hass, "mode") == "fallback"
    assert float(value(hass, "grid_limit")) == 5400
    assert float(value(hass, "basket")) == 1375
    assert float(value(hass, "margin")) == 25
    assert value(hass, "used") == "unknown"


async def test_next_window_starts_from_the_last_reading_before_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(START)
    set_sources(hass, 1650, 12345.0, 0)
    await setup_entry(hass)

    # The meter reports every few seconds, also just before the boundary.
    freezer.move_to("2026-11-15T03:14:58+00:00")
    set_sources(hass, 1650, 12345.5, 0)
    freezer.move_to("2026-11-15T03:15:02+00:00")
    # The grid power arrives first and rolls the window over, before the counter is fresh.
    hass.states.async_set(GRID, "1.65", {"unit_of_measurement": "kW"})
    await hass.async_block_till_done()
    assert value(hass, "mode") == "pulse_on"
    assert float(value(hass, "house_power")) == 1650
    assert float(value(hass, "allowed_power")) == pytest.approx(4340)
    # The counter's 4 s since its last report, at the grid power.
    assert float(value(hass, "used")) == pytest.approx(1650 * 4 / 3600, abs=1)

    # 1.2 kWh imported 10 min in: the house reserve takes the rest, so the load stops.
    freezer.move_to("2026-11-15T03:25:02+00:00")
    set_sources(hass, 5790, 12346.7, 4140)
    await tick(hass)
    assert float(value(hass, "used")) == pytest.approx(1200)
    assert value(hass, "mode") == "pulse_off"
    assert float(value(hass, "allowed_power")) == 0
    assert float(value(hass, "charge_minutes")) == 0


async def test_without_a_recent_reading_the_first_one_after_the_boundary_counts(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(START)
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass)
    freezer.move_to("2026-11-15T03:15:01+00:00")
    set_sources(hass, 1000, 100.2, 0)
    await tick(hass)
    assert float(value(hass, "used")) == 0
    assert value(hass, "mode") == "continuous"


async def test_stale_sources_fall_back(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(START)
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass)
    freezer.move_to(START + timedelta(minutes=10, seconds=5))
    await tick(hass)
    assert value(hass, "mode") == "fallback"
    assert float(value(hass, "allowed_power")) == 0
    assert value(hass, "house_power") == "unknown"


async def test_an_unknown_load_power_counts_as_zero(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The house is then over-estimated: the load gets less room, never more."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 5000, 100.0, 4000)
    await setup_entry(hass)
    freezer.move_to("2026-11-15T03:15:01+00:00")
    set_sources(hass, 5000, 100.0, 4000)
    await tick(hass)
    assert float(value(hass, "house_power")) == 1000

    for state, attributes in (("unavailable", {}), ("n/a", {"unit_of_measurement": "W"})):
        hass.states.async_set(EV, state, attributes)
        await tick(hass)
        assert value(hass, "mode") != "fallback"
        assert float(value(hass, "house_power")) == 5000
    hass.states.async_remove(EV)
    await tick(hass)
    assert value(hass, "mode") != "fallback"


async def test_a_load_that_only_reports_changes_can_start(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A plug at 0 W writes nothing new: its state ages, but the load must not lock out."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass)
    freezer.move_to("2026-11-15T03:20:00+00:00")
    hass.states.async_set(GRID, "1000", {"unit_of_measurement": "W"})
    hass.states.async_set(IMPORT, "100.4", {"unit_of_measurement": "kWh"})
    await tick(hass)  # the load's 0 W is five minutes old now
    assert value(hass, "mode") != "fallback"
    assert float(value(hass, "allowed_power")) > 0


async def test_unsupported_units_fall_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass)
    freezer.move_to("2026-11-15T03:15:01+00:00")
    hass.states.async_set(EV, "0", {"unit_of_measurement": "A"})
    hass.states.async_set(IMPORT, "100", {"unit_of_measurement": "m³"})
    await tick(hass)
    assert value(hass, "mode") == "fallback"
    assert "Unsupported power unit A on sensor.ev_power" in caplog.text
    assert "Unsupported energy unit m³ on sensor.p1_import" in caplog.text


async def test_counter_going_backwards_falls_back_for_the_window(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass)
    freezer.move_to("2026-11-15T03:15:01+00:00")
    set_sources(hass, 1000, 0.5, 0)  # a meter swap
    await tick(hass)
    assert value(hass, "mode") == "fallback"
    assert "went backwards" in caplog.text
    freezer.move_to("2026-11-15T03:15:06+00:00")
    set_sources(hass, 1000, 0.6, 0)
    await tick(hass)
    assert value(hass, "mode") == "fallback"


async def test_window_average_source_and_voltage_chain(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The meter's running average needs no history, so a mid-window start is exact."""
    freezer.move_to(START)
    hass.states.async_set(GRID, "1000", {"unit_of_measurement": "W"})
    hass.states.async_set(EV, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(AVERAGE, "333.33", {"unit_of_measurement": "W"})  # 83.3 Wh so far
    # L1 from the charger, L2 from the meter, L3 from neither: 235 + 231 + 230 V.
    for phase, voltage in enumerate(["235", "unavailable", "0"], 1):
        hass.states.async_set(f"sensor.charger_l{phase}", voltage, {"unit_of_measurement": "V"})
    hass.states.async_set("sensor.meter_l2", "231", {"unit_of_measurement": "V"})
    sources: dict[str, Any] = {
        "name": "Peak Basket",
        "profile": "be_flanders",
        "grid_power": GRID,
        "window_average": AVERAGE,
        "load_power": EV,
        "voltages": [f"sensor.charger_l{phase}" for phase in (1, 2, 3)],
        "meter_voltages": ["sensor.meter_l1", "sensor.meter_l2"],
    }
    entry = await setup_entry(hass, sources)

    assert value(hass, "mode") == "continuous"
    assert float(value(hass, "used")) == 83  # 83.3 Wh, published per Wh
    # (5400 W x 900 s - 300000 J) over the 600 s left.
    assert float(value(hass, "grid_limit")) == pytest.approx(7600, abs=1)
    assert next(iter(entry.runtime_data.data.loads.values())).p_load_min_w == pytest.approx(6 * 696)

    hass.states.async_set(AVERAGE, "unavailable")
    await tick(hass)
    assert value(hass, "mode") == "fallback"


async def test_month_peak_raises_the_target(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(START)
    set_sources(hass, 1000, 100.0, 0)
    # The monthly peak changes rarely: its age does not matter.
    hass.states.async_set("sensor.month_peak", "6.1", {"unit_of_measurement": "kW"})
    freezer.move_to(START + timedelta(minutes=5))
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass, {**SOURCES, "month_peak": "sensor.month_peak"})
    assert float(value(hass, "target")) == 6100
    assert float(value(hass, "basket")) == 1525


async def test_restart_mid_window_restores_the_counter_from_history(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(START)
    set_sources(hass, 1000, 100.1, 0)
    history = [
        {"state": "ignored, a compressed row"},
        State(IMPORT, "unknown"),
        State(IMPORT, "100.0"),
    ]
    with patch("custom_components.peak_basket.coordinator.get_instance") as get_instance:
        get_instance.return_value.async_add_executor_job = AsyncMock(return_value={IMPORT: history})
        await setup_entry(hass)
    assert value(hass, "mode") == "continuous"
    assert float(value(hass, "used")) == pytest.approx(100)


def _recorder(rows: dict[str, list[Any]]) -> MagicMock:
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value=rows)
    return recorder


async def _restart_before_the_meter(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, row: State
) -> None:
    """Start while the meter has no state yet, then let it report 100 Wh into the window."""
    freezer.move_to(START)
    hass.states.async_set(GRID, "1000", {"unit_of_measurement": "W"})
    hass.states.async_set(EV, "0", {"unit_of_measurement": "W"})
    with patch(
        "custom_components.peak_basket.coordinator.get_instance",
        return_value=_recorder({IMPORT: [row]}),
    ):
        await setup_entry(hass)
    freezer.move_to(START + timedelta(seconds=10))
    set_sources(hass, 1000, 100.1, 0)
    await tick(hass)


async def test_restart_before_the_meter_reports_takes_the_unit_from_history(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _restart_before_the_meter(
        hass, freezer, State(IMPORT, "100.0", {"unit_of_measurement": "kWh"})
    )
    assert value(hass, "mode") == "continuous"
    assert float(value(hass, "used")) == pytest.approx(100)


async def test_restart_before_the_meter_reports_takes_the_unit_from_the_registry(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    er.async_get(hass).async_get_or_create(
        "sensor", "p1", "import", suggested_object_id="p1_import", unit_of_measurement="kWh"
    )
    await _restart_before_the_meter(hass, freezer, State(IMPORT, "100.0"))
    assert value(hass, "mode") == "continuous"
    assert float(value(hass, "used")) == pytest.approx(100)


async def test_restart_without_a_known_unit_does_not_guess_one(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _restart_before_the_meter(hass, freezer, State(IMPORT, "100.0"))
    assert value(hass, "mode") == "fallback"
    assert value(hass, "used") == "unknown"


async def test_restart_without_a_row_at_the_window_start_falls_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(START)
    set_sources(hass, 1000, 100.1, 0)
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value={})
    with patch("custom_components.peak_basket.coordinator.get_instance", return_value=recorder):
        await setup_entry(hass)
    assert value(hass, "mode") == "fallback"


async def test_unload_stops_the_coordinator(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(START)
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not coordinator._unsubs  # noqa: SLF001


async def _continuous_entry(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Set up in continuous mode at 03:15:01, with its first publication at that moment."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    await setup_entry(hass)
    freezer.move_to("2026-11-15T03:15:01+00:00")
    set_sources(hass, 1000, 100.0, 0)  # unchanged values fire no event: the tick evaluates
    await tick(hass)
    assert value(hass, "mode") == "continuous"


async def test_values_are_rounded_so_small_changes_write_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _continuous_entry(hass, freezer)
    assert float(value(hass, "house_power")) % 10 == 0
    assert float(value(hass, "grid_limit")) % 10 == 0
    assert float(value(hass, "used")) == int(float(value(hass, "used")))
    assert float(value(hass, "charge_minutes")) == int(float(value(hass, "charge_minutes")))


async def test_publishes_at_most_every_tick_while_nothing_drops(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _continuous_entry(hass, freezer)
    # A meter reporting every second, with a slightly lower house load: the load may draw
    # a little more, which can wait for the next tick.
    freezer.move_to("2026-11-15T03:15:02+00:00")
    hass.states.async_set(GRID, "900", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert float(value(hass, "house_power")) == 1000  # not published yet
    freezer.move_to("2026-11-15T03:15:06+00:00")
    hass.states.async_set(GRID, "910", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert float(value(hass, "house_power")) == 910


async def test_a_lower_allowed_power_is_published_at_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _continuous_entry(hass, freezer)
    allowed = float(value(hass, "allowed_power"))
    freezer.move_to("2026-11-15T03:15:02+00:00")
    hass.states.async_set(GRID, "3000", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert float(value(hass, "allowed_power")) < allowed


async def test_house_reserve_and_margin_are_diagnostic(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _continuous_entry(hass, freezer)
    registry = er.async_get(hass)
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    for key in ("house_reserve", "margin"):
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
        assert entity_id is not None
        reg_entry = registry.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_previous_windows_average_counts_as_nothing_used(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The meter resets its average a second or two after the boundary.

    Until then it still shows the previous window's average: that must not count as
    energy already used in the new window.
    """
    freezer.move_to("2026-11-15T03:14:57+00:00")
    hass.states.async_set(GRID, "5170", {"unit_of_measurement": "W"})
    hass.states.async_set(EV, "4270", {"unit_of_measurement": "W"})
    hass.states.async_set(AVERAGE, "5300", {"unit_of_measurement": "W"})
    sources: dict[str, Any] = {**SOURCES, "window_average": AVERAGE}
    del sources["grid_import_energy"]
    await setup_entry(hass, sources)
    assert float(value(hass, "used")) == pytest.approx(1325)

    # New window, the average not yet reset: a grid update lands first.
    freezer.move_to("2026-11-15T03:15:01+00:00")
    hass.states.async_set(GRID, "5171", {"unit_of_measurement": "W"})
    await tick(hass)  # a higher limit waits for the tick
    # Only the second since the boundary, at the grid power.
    assert float(value(hass, "used")) == pytest.approx(5171 / 3600, abs=1)
    assert value(hass, "mode") == "continuous"
    assert float(value(hass, "allowed_power")) > 4000

    # The reset arrives.
    freezer.move_to("2026-11-15T03:15:02+00:00")
    hass.states.async_set(AVERAGE, "3", {"unit_of_measurement": "W"})
    await tick(hass)
    assert float(value(hass, "used")) == 1  # 0.75 Wh, published per Wh
    assert value(hass, "mode") == "continuous"


async def test_an_old_average_reported_after_the_boundary_is_capped(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A reader can poll the meter just after the boundary, before the meter resets.

    Its reading then carries the previous window's average with a fresh timestamp. No
    grid connection imports 1325 Wh in a second: the used energy is capped.
    """
    freezer.move_to("2026-11-15T03:14:57+00:00")
    hass.states.async_set(GRID, "5170", {"unit_of_measurement": "W"})
    hass.states.async_set(EV, "4270", {"unit_of_measurement": "W"})
    hass.states.async_set(AVERAGE, "5300", {"unit_of_measurement": "W"})
    sources: dict[str, Any] = {**SOURCES, "window_average": AVERAGE}
    del sources["grid_import_energy"]
    await setup_entry(hass, sources)

    freezer.move_to("2026-11-15T03:15:00.500+00:00")
    hass.states.async_set(AVERAGE, "5301", {"unit_of_measurement": "W"})
    await tick(hass)
    assert float(value(hass, "used")) <= 10  # 0.5 s x 69 kW
    assert value(hass, "mode") == "continuous"


def _average_sources() -> dict[str, Any]:
    sources = {**SOURCES, "window_average": AVERAGE}
    del sources["grid_import_energy"]
    return sources


async def _start_before_the_meter(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Start right after a restart: everything is there but the grid power."""
    freezer.move_to(START)
    hass.states.async_set(AVERAGE, "400", {"unit_of_measurement": "W"})
    hass.states.async_set(EV, "0", {"unit_of_measurement": "W"})
    await setup_entry(hass, _average_sources())


async def test_start_before_the_meter_reports_is_unavailable_not_0_w(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _start_before_the_meter(hass, freezer)
    assert value(hass, "allowed_power") == "unavailable"
    assert value(hass, "mode") == "unavailable"
    assert value(hass, "grid_limit") == "unavailable"


async def test_the_meter_s_first_state_is_published_at_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _start_before_the_meter(hass, freezer)
    freezer.move_to(START + timedelta(milliseconds=70))
    hass.states.async_set(AVERAGE, "400", {"unit_of_measurement": "W"}, force_update=True)
    hass.states.async_set(GRID, "1000", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert value(hass, "mode") == "continuous"
    assert float(value(hass, "allowed_power")) > 0


async def test_a_meter_still_missing_after_the_maximum_age_gives_the_fallback(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _start_before_the_meter(hass, freezer)
    freezer.move_to(START + timedelta(seconds=55))
    await tick(hass)
    assert value(hass, "mode") == "unavailable"
    freezer.move_to(START + timedelta(seconds=61))
    await tick(hass)
    assert value(hass, "mode") == "fallback"
    assert float(value(hass, "grid_limit")) == 5400
