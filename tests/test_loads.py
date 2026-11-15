"""Several loads on one grid connection, and the migration from one load per entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.peak_basket import async_migrate_entry
from custom_components.peak_basket.const import DOMAIN

from .conftest import EV, GRID, IMPORT, set_sources, setup_entry, tick, value

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
    assert ev_limit - ev_allowed == pytest.approx(1000, abs=10)
    assert boiler_limit - boiler_allowed == pytest.approx(1000 + 4140, abs=10)


async def test_priority_decides_who_yields(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, _, boiler = await _two_loads(hass, freezer, boiler_priority=0)
    boiler_limit = float(_load_value(hass, entry, boiler, "grid_limit"))
    boiler_allowed = float(_load_value(hass, entry, boiler, "allowed_power"))
    assert boiler_limit - boiler_allowed == pytest.approx(1000, abs=10)


async def test_unknown_load_power_makes_the_other_loads_fall_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, ev, boiler = await _two_loads(hass, freezer, boiler_priority=2)
    hass.states.async_set(BOILER, "unavailable")
    await tick(hass)
    # The EV cannot tell the boiler's share from the house's: safe fallback for both.
    assert _load_value(hass, entry, ev, "mode") == "fallback"
    assert _load_value(hass, entry, boiler, "mode") == "fallback"
    assert value(hass, "house_power") == "unknown"


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
