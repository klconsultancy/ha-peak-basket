"""Several loads on one grid connection, and the migration from one load per entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.peak_basket import async_migrate_entry
from custom_components.peak_basket.const import DOMAIN

from .conftest import EV, GRID, IMPORT, SOURCES, set_sources, setup_entry, tick, value

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

BOILER = "sensor.boiler_power"


async def _add_boiler(hass: HomeAssistant, entry: ConfigEntry, priority: int) -> str:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Boiler", "load_power": BOILER, "priority": priority}
    )
    await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    return next(s.subentry_id for s in entry.subentries.values() if s.title == "Boiler")


def _load_value(hass: HomeAssistant, entry: ConfigEntry, subentry_id: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{subentry_id}_{key}"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def _two_loads(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, boiler_priority: int
) -> tuple[ConfigEntry, str, str]:
    """House 1000 W, EV 4140 W and boiler 2000 W, 1 s into a fresh window."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 7140, 100.0, 4140)
    hass.states.async_set(BOILER, "2000", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass)
    boiler = await _add_boiler(hass, entry, boiler_priority)
    (ev,) = (s.subentry_id for s in entry.subentries.values() if s.title != "Boiler")
    freezer.move_to("2026-11-15T03:15:01+00:00")
    set_sources(hass, 7140, 100.0, 4140)
    hass.states.async_set(BOILER, "2000", {"unit_of_measurement": "W"})
    await tick(hass)
    return entry, ev, boiler


async def test_connection_house_excludes_every_load(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _two_loads(hass, freezer, boiler_priority=2)
    assert float(value(hass, "house_power")) == 1000


async def test_a_lower_priority_load_yields_to_a_higher_one(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, ev, boiler = await _two_loads(hass, freezer, boiler_priority=2)
    # The EV sees only the house: the boiler yields. The boiler sees the EV as house.
    ev_allowed = float(_load_value(hass, entry, ev, "allowed_power"))
    boiler_allowed = float(_load_value(hass, entry, boiler, "allowed_power"))
    ev_limit = float(_load_value(hass, entry, ev, "grid_limit"))
    boiler_limit = float(_load_value(hass, entry, boiler, "grid_limit"))
    assert ev_allowed > boiler_allowed
    # The grid limit is the whole grid's: the house, this load, and the loads that yield.
    assert ev_limit - ev_allowed == pytest.approx(1000 + 2000, abs=10)
    assert boiler_limit - boiler_allowed == pytest.approx(1000 + 4140, abs=10)


async def test_priority_decides_who_yields(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, ev, boiler = await _two_loads(hass, freezer, boiler_priority=0)
    boiler_limit = float(_load_value(hass, entry, boiler, "grid_limit"))
    boiler_allowed = float(_load_value(hass, entry, boiler, "allowed_power"))
    ev_limit = float(_load_value(hass, entry, ev, "grid_limit"))
    ev_allowed = float(_load_value(hass, entry, ev, "allowed_power"))
    # Now the boiler goes first and the EV yields: the EV sees the boiler as house.
    assert boiler_limit - boiler_allowed == pytest.approx(1000 + 4140, abs=10)
    assert ev_limit - ev_allowed == pytest.approx(1000 + 2000, abs=10)


async def test_an_unknown_lower_priority_load_does_not_stop_a_higher_one(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, ev, boiler = await _two_loads(hass, freezer, boiler_priority=2)
    hass.states.async_set(BOILER, "unavailable")
    await tick(hass)
    # The boiler counts as 0 W: its 2000 W on the grid count as house, on the safe side.
    assert _load_value(hass, entry, ev, "mode") != "fallback"
    assert _load_value(hass, entry, boiler, "mode") != "fallback"
    assert float(value(hass, "house_power")) == 3000


async def test_each_load_has_its_own_device(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, _, boiler = await _two_loads(hass, freezer, boiler_priority=2)
    devices = dr.async_get(hass)
    connection = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    load = devices.async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{boiler}"), entry.entry_id
    )
    assert connection is not None
    assert load is not None
    assert load.name == "Boiler"
    assert load.via_device_id == connection.id


async def test_removing_a_load_removes_its_sensors(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, _, boiler = await _two_loads(hass, freezer, boiler_priority=2)
    assert hass.config_entries.async_remove_subentry(entry, boiler)
    await hass.async_block_till_done()
    assert boiler not in entry.runtime_data.data.loads
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{boiler}_allowed_power"
        )
        is None
    )


async def test_without_loads_the_connection_still_counts(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    (load,) = entry.subentries
    assert hass.config_entries.async_remove_subentry(entry, load)
    await hass.async_block_till_done()
    freezer.move_to("2026-11-15T03:15:01+00:00")
    set_sources(hass, 1000, 100.0, 0)
    await tick(hass)
    assert entry.runtime_data.data.loads == {}
    assert float(value(hass, "house_power")) == 1000
    assert float(value(hass, "used")) == 0


async def test_migrates_a_version_1_entry_and_keeps_its_entity_ids(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-15T03:05:00+00:00")
    set_sources(hass, 1000, 100.0, 0)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Peak Basket",
        version=1,
        data={
            "profile": "be_flanders",
            "grid_power": GRID,
            "grid_import_energy": IMPORT,
            "load_power": EV,
            "voltages": ["sensor.charger_l1"],
            "voltages_fallback": ["sensor.meter_l1"],
        },
        options={
            "peak_goal_w": 6000.0,
            "margin_w": 150.0,
            "reaction_s": 60.0,
            "min_charge_s": 120.0,
            "extra_a": 1.0,
            "min_a": 6.0,
            "max_a": 32.0,
            "max_age_s": 30.0,
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = {
        key: registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}_{key}",
            config_entry=entry,
            suggested_object_id=f"peak_basket_{key}",
        ).entity_id
        for key in ("allowed_load_power", "grid_limit", "mode", "basket")
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert dict(entry.data) == {
        "profile": "be_flanders",
        "grid_power": GRID,
        "grid_import_energy": IMPORT,
        "meter_voltages": ["sensor.meter_l1"],
    }
    assert dict(entry.options) == {"peak_goal_w": 6000.0, "margin_w": 150.0, "max_age_s": 30.0}
    (load,) = entry.subentries.values()
    assert load.title == "Load"
    assert dict(load.data) == {
        "load_power": EV,
        "voltages": ["sensor.charger_l1"],
        "priority": 1,
        "reaction_s": 60.0,
        "min_charge_s": 120.0,
        "extra_a": 1.0,
        "min_a": 6.0,
        "max_a": 32.0,
    }

    # The load's sensors moved to the load, under the same entity ids.
    allowed = registry.async_get(old["allowed_load_power"])
    assert allowed is not None
    assert allowed.unique_id == f"{entry.entry_id}_{load.subentry_id}_allowed_power"
    assert allowed.config_subentry_id == load.subentry_id
    grid_limit = registry.async_get(old["grid_limit"])
    assert grid_limit is not None
    assert grid_limit.unique_id == f"{entry.entry_id}_{load.subentry_id}_grid_limit"
    # The connection's sensors stay where they were.
    basket = registry.async_get(old["basket"])
    assert basket is not None
    assert basket.unique_id == f"{entry.entry_id}_basket"
    assert basket.config_subentry_id is None
    assert hass.states.get(old["allowed_load_power"]) is not None


async def test_migrates_a_version_1_entry_without_voltages(hass: HomeAssistant) -> None:
    set_sources(hass, 1000, 100.0, 0)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "profile": "be_flanders",
            "grid_power": GRID,
            "grid_import_energy": IMPORT,
            "load_power": EV,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "meter_voltages" not in entry.data
    (load,) = entry.subentries.values()
    assert "voltages" not in load.data
    assert load.data["reaction_s"] == 90.0


async def test_a_newer_entry_version_is_refused(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_migration_leaves_a_version_2_entry_alone(hass: HomeAssistant) -> None:
    """Home Assistant also migrates on a minor version bump: nothing to move then."""
    entry = MockConfigEntry(domain=DOMAIN, version=2, data={"grid_power": GRID})
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert dict(entry.data) == {"grid_power": GRID}
    assert entry.subentries == {}


async def test_migration_refuses_an_unknown_newer_version(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={})
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)


async def test_migrated_load_is_named_in_the_system_language(hass: HomeAssistant) -> None:
    hass.config.language = "nl"
    set_sources(hass, 1000, 100.0, 0)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "profile": "be_flanders",
            "grid_power": GRID,
            "grid_import_energy": IMPORT,
            "load_power": EV,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    (load,) = entry.subentries.values()
    assert load.title == "Afnemer"


async def test_a_starting_load_keeps_its_room_from_the_loads_after_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Both loads idle: the EV may start, so the boiler must not count on the same room."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    hass.states.async_set(BOILER, "0", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass)
    boiler = await _add_boiler(hass, entry, 2)
    (ev,) = (s.subentry_id for s in entry.subentries.values() if s.title != "Boiler")
    freezer.move_to("2026-11-15T03:15:01+00:00")
    set_sources(hass, 1000, 100.0, 0)
    hass.states.async_set(BOILER, "0", {"unit_of_measurement": "W"})
    await tick(hass)
    ev_allowed = float(_load_value(hass, entry, ev, "allowed_power"))
    boiler_limit = float(_load_value(hass, entry, boiler, "grid_limit"))
    boiler_allowed = float(_load_value(hass, entry, boiler, "allowed_power"))
    assert _load_value(hass, entry, ev, "mode") in {"continuous", "pulse_on"}
    assert ev_allowed > 0
    # The boiler counts the EV's claim as house.
    assert boiler_limit - boiler_allowed == pytest.approx(1000 + ev_allowed, abs=20)


async def test_a_running_load_claims_only_what_it_draws(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Once the EV draws, the grid measures it: no claim on top."""
    entry, _, boiler = await _two_loads(hass, freezer, boiler_priority=2)
    boiler_limit = float(_load_value(hass, entry, boiler, "grid_limit"))
    boiler_allowed = float(_load_value(hass, entry, boiler, "allowed_power"))
    assert boiler_limit - boiler_allowed == pytest.approx(1000 + 4140, abs=10)


async def _idle_pair(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, **ev: str
) -> tuple[ConfigEntry, str, str]:
    """House 1000 W, EV and boiler idle at 0 W, 1 s into the 03:15 window."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    set_sources(hass, 1000, 100.0, 0)
    hass.states.async_set(BOILER, "0", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass, {**SOURCES, **ev})
    boiler = await _add_boiler(hass, entry, 2)
    (car,) = (s.subentry_id for s in entry.subentries.values() if s.title != "Boiler")
    await _idle_at(hass, freezer, "2026-11-15T03:15:01+00:00")
    return entry, car, boiler


async def _idle_at(hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: str) -> None:
    freezer.move_to(at)
    set_sources(hass, 1000, 100.0, 0)
    hass.states.async_set(BOILER, "0", {"unit_of_measurement": "W"})
    await tick(hass)


def _boiler_sees(hass: HomeAssistant, entry: ConfigEntry, boiler: str) -> float:
    """The house as the boiler counts it: its grid limit minus its allowed power."""
    limit = float(_load_value(hass, entry, boiler, "grid_limit"))
    return limit - float(_load_value(hass, entry, boiler, "allowed_power"))


async def test_an_idle_load_claims_only_for_its_reaction_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A car that is not plugged in must not keep the boiler out all day."""
    entry, car, boiler = await _idle_pair(hass, freezer)
    car_allowed = float(_load_value(hass, entry, car, "allowed_power"))
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000 + car_allowed, abs=20)

    await _idle_at(hass, freezer, "2026-11-15T03:16:40+00:00")  # 99 s > 90 s reaction
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000, abs=10)
    await _idle_at(hass, freezer, "2026-11-15T03:20:00+00:00")
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000, abs=10)


async def test_a_lapsed_claim_comes_back_in_a_new_window_or_after_drawing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, car, boiler = await _idle_pair(hass, freezer)
    await _idle_at(hass, freezer, "2026-11-15T03:16:40+00:00")
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000, abs=10)

    await _idle_at(hass, freezer, "2026-11-15T03:30:01+00:00")
    car_allowed = float(_load_value(hass, entry, car, "allowed_power"))
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000 + car_allowed, abs=20)

    await _idle_at(hass, freezer, "2026-11-15T03:31:40+00:00")
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000, abs=10)
    # The car draws for a moment, then stops: a fresh claim.
    freezer.move_to("2026-11-15T03:31:45+00:00")
    set_sources(hass, 5140, 100.1, 4140)
    hass.states.async_set(BOILER, "0", {"unit_of_measurement": "W"})
    await tick(hass)
    await _idle_at(hass, freezer, "2026-11-15T03:31:50+00:00")
    car_allowed = float(_load_value(hass, entry, car, "allowed_power"))
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000 + car_allowed, abs=20)


async def test_a_load_without_demand_claims_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set("binary_sensor.car_plugged_in", "off")
    entry, car, boiler = await _idle_pair(hass, freezer, demand="binary_sensor.car_plugged_in")
    # It may still start: it keeps its own allowed power.
    assert float(_load_value(hass, entry, car, "allowed_power")) > 0
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000, abs=10)


async def test_a_load_with_demand_claims_until_it_draws(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set("binary_sensor.car_plugged_in", "on")
    entry, car, boiler = await _idle_pair(hass, freezer, demand="binary_sensor.car_plugged_in")
    await _idle_at(hass, freezer, "2026-11-15T03:20:00+00:00")  # well past the reaction time
    car_allowed = float(_load_value(hass, entry, car, "allowed_power"))
    assert _boiler_sees(hass, entry, boiler) == pytest.approx(1000 + car_allowed, abs=20)


HEAT_PUMP = "sensor.heat_pump_power"


async def _add_heat_pump(hass: HomeAssistant, entry: ConfigEntry, priority: int = 0) -> str:
    """Add a heat pump that is only measured: Peak Basket does not steer it."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_USER}
    )
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Heat pump", "load_power": HEAT_PUMP, "priority": priority, "steer": False},
    )
    await hass.async_block_till_done()
    return next(s.subentry_id for s in entry.subentries.values() if s.title == "Heat pump")


def _report(
    hass: HomeAssistant, grid_w: float, import_kwh: float, ev_w: float, hp_w: float
) -> None:
    set_sources(hass, grid_w, import_kwh, ev_w)
    hass.states.async_set(HEAT_PUMP, str(hp_w), {"unit_of_measurement": "W"})


async def test_a_measured_only_load_counts_as_house_for_the_others(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """House 1000 W, heat pump 2000 W, EV 4140 W: the EV sees 3000 W of house either way."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    _report(hass, 7140, 100.0, 4140, 2000)
    entry = await setup_entry(hass)
    freezer.move_to("2026-11-15T03:15:01+00:00")
    _report(hass, 7140, 100.0, 4140, 2000)
    await tick(hass)
    without = {k: value(hass, k) for k in ("allowed_power", "grid_limit", "mode")}

    # After the EV in priority, it still does not yield to it.
    await _add_heat_pump(hass, entry, priority=2)
    # The reload loses the import counter's start: compare at the same point of the next window.
    freezer.move_to("2026-11-15T03:30:01+00:00")
    _report(hass, 7140, 100.0, 4140, 2000)
    await tick(hass)
    assert {k: value(hass, k) for k in without} == without


async def _with_heat_pump(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, **sources: str
) -> tuple[ConfigEntry, str, str]:
    """House 1000 W, EV 4140 W and a measured heat pump 2000 W, 1 s into a fresh window."""
    freezer.move_to("2026-11-15T03:14:59+00:00")
    _report(hass, 7140, 100.0, 4140, 2000)
    entry = await setup_entry(hass, {**SOURCES, **sources})
    heat_pump = await _add_heat_pump(hass, entry, priority=0)
    (ev,) = (s.subentry_id for s in entry.subentries.values() if s.title != "Heat pump")
    freezer.move_to("2026-11-15T03:15:01+00:00")
    _report(hass, 7140, 100.0, 4140, 2000)
    await tick(hass)
    return entry, ev, heat_pump


def _load_keys(hass: HomeAssistant, entry: ConfigEntry, subentry_id: str) -> set[str]:
    prefix = f"{entry.entry_id}_{subentry_id}_"
    return {
        e.unique_id.removeprefix(prefix)
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.unique_id.startswith(prefix)
    }


async def test_a_measured_only_load_has_only_its_import_sensors(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set("sensor.pv_power", "0", {"unit_of_measurement": "W"})
    entry, _, heat_pump = await _with_heat_pump(hass, freezer, pv_power="sensor.pv_power")
    assert _load_keys(hass, entry, heat_pump) == {"load_grid", "load_solar", "load_reserve"}


async def test_the_basket_stacks_up_with_a_measured_only_load(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """House reserve, the heat pump's reserve and the EV's room: what remains."""
    entry, ev, heat_pump = await _with_heat_pump(hass, freezer)
    house_reserve = float(value(hass, "house_reserve"))
    heat_pump_reserve = float(_load_value(hass, entry, heat_pump, "load_reserve"))
    room = float(_load_value(hass, entry, ev, "load_room"))
    assert heat_pump_reserve == pytest.approx(2000 * 899 / 3600, abs=1)
    assert house_reserve + heat_pump_reserve + room == pytest.approx(
        float(value(hass, "remaining")), abs=2
    )


async def test_who_drew_from_the_grid_adds_up_with_a_measured_only_load(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, ev, heat_pump = await _with_heat_pump(hass, freezer)
    freezer.move_to("2026-11-15T03:16:01+00:00")
    _report(hass, 7140, 100.119, 4140, 2000)  # 7140 W for 60 s: 119 Wh
    await tick(hass)
    heat_pump_grid = float(_load_value(hass, entry, heat_pump, "load_grid"))
    ev_grid = float(_load_value(hass, entry, ev, "load_grid"))
    house_grid = float(value(hass, "house_grid"))
    assert heat_pump_grid == pytest.approx(2000 * 61 / 3600, abs=1)
    assert heat_pump_grid + ev_grid + house_grid == pytest.approx(float(value(hass, "used")), abs=1)
    # The house is what neither load drew: the heat pump is out of it.
    assert float(value(hass, "house_power")) == 1000


async def test_solar_goes_to_the_steered_loads_before_a_measured_only_one(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The heat pump goes first in priority, but solar covers the EV first."""
    hass.states.async_set("sensor.pv_power", "3000", {"unit_of_measurement": "W"})
    entry, ev, heat_pump = await _with_heat_pump(hass, freezer, pv_power="sensor.pv_power")
    freezer.move_to("2026-11-15T03:16:01+00:00")
    _report(hass, 4140, 100.069, 4140, 2000)  # 4140 W for 60 s: 69 Wh
    hass.states.async_set("sensor.pv_power", "3000", {"unit_of_measurement": "W"})
    await tick(hass)
    assert float(_load_value(hass, entry, ev, "load_solar")) == pytest.approx(3000 / 60, abs=1)
    assert float(_load_value(hass, entry, heat_pump, "load_solar")) == 0


async def _reconfigure_load(
    hass: HomeAssistant, entry: ConfigEntry, subentry_id: str, *, steer: bool
) -> None:
    subentry = entry.subentries[subentry_id]
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": subentry.title,
            "load_power": subentry.data["load_power"],
            "priority": subentry.data["priority"],
            "steer": steer,
        },
    )
    if steer:
        assert result["step_id"] == "reconfigure_steering"
        result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()


def _entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    return {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


async def test_steering_a_measured_only_load_later_keeps_its_entity_ids(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, _, heat_pump = await _with_heat_pump(hass, freezer)
    before = _entity_ids(hass, entry)

    await _reconfigure_load(hass, entry, heat_pump, steer=True)
    assert entry.subentries[heat_pump].data["steer"] is True
    assert _load_keys(hass, entry, heat_pump) >= {"allowed_power", "grid_limit", "mode"}
    # A steered load's share is in its room, not in a reserve of its own.
    assert "load_reserve" not in _load_keys(hass, entry, heat_pump)
    after = _entity_ids(hass, entry)
    kept = {k: v for k, v in before.items() if not k.endswith("_load_reserve")}
    assert {k: v for k, v in after.items() if k in before} == kept

    await _reconfigure_load(hass, entry, heat_pump, steer=False)
    assert dict(entry.subentries[heat_pump].data) == {
        "load_power": HEAT_PUMP,
        "priority": 0,
        "steer": False,
    }
    assert _load_keys(hass, entry, heat_pump) == {"load_grid", "load_reserve"}
    assert _entity_ids(hass, entry) == before


async def test_the_basket_stacks_up_when_solar_covers_part_of_a_measured_only_load(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Grid 1000 W, heat pump 2000 W, EV idle: solar gives the heat pump the other 1000 W."""
    hass.states.async_set("sensor.pv_power", "1000", {"unit_of_measurement": "W"})
    entry, ev, heat_pump = await _with_heat_pump(hass, freezer, pv_power="sensor.pv_power")
    freezer.move_to("2026-11-15T03:15:02+00:00")
    _report(hass, 1000, 100.0, 0, 2000)
    await tick(hass)
    heat_pump_reserve = float(_load_value(hass, entry, heat_pump, "load_reserve"))
    room = float(_load_value(hass, entry, ev, "load_room"))
    assert float(value(hass, "house_reserve")) == 0
    assert heat_pump_reserve == pytest.approx(1000 * 898 / 3600, abs=1)
    assert heat_pump_reserve + room == pytest.approx(float(value(hass, "remaining")), abs=2)
